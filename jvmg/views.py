from django.shortcuts import render
from django.http import HttpResponse
from difflib import SequenceMatcher
from SPARQLWrapper import SPARQLWrapper, XML, JSONLD, TURTLE, JSON
from rdflib import URIRef, BNode
from django.conf import settings
import elasticsearch


def rewrite_URL(URL):
    return URL.replace(settings.DATASET_BASE, str(settings.WEB_BASE))


def main(request, path):
    """
    main entry point for URI lookups. reads the accept_headers/formats to returns the rdf data as requested.
    this includes:
    - "application/rdf+xml" returns the rdf-data in xml
    - "text/turtle" returns the rdf-data in ttl
    - "application/json" returns the rdf-data in json
    - otherwise a html site is created
    """
    # get accept headers
    accept_header = request.headers.get("Accept", "text/html")
    accepted_formats = list(map(lambda format: format.split(";")[0], accept_header.split(",")))
    for accepted_format in accepted_formats:
        if accepted_format == "application/rdf+xml":
            return get_data(path, XML, "application/rdf+xml")
        elif accepted_format == "text/turtle":
            return get_data(path, TURTLE, "text/turtle")
        elif accepted_format == "application/json":
            return get_data(path, JSONLD, "application/json")
        else:
            context = get_data(path, JSONLD, "text/html")
            return render(request, "jvmg/main.html", context)


def get_data(path, data_format, content_type):
    """
    Uses the sparql_query and sparql_endpoint to get the rdf_data (defined in setting.py).
    Dependent on the data_format and content_type (xml, ttl and jsonld) we return
    the sparql_result directly or create the following data structure which is used to create HTML.

    Data structure:
    quads_by_graph = {
        graph_uris: {
            "graph_label": graph_label_info,
            "predicates": {
               "predicate_data": predicate_label_info,
               "is_subject": boolean     # if current predicate is a backlink
               "object": [object_label_info]
          }
       }
    }

    label_info stores URI and their labels or just the labels if it is a literal
    label_info = {
        "labels" = [labels],
        "uri" = URI or None
    }
    """
    resource_uri = URIRef(settings.DATASET_BASE + path)

    # we don't use format() to avoid escaping all the curly braces in sparql queries
    query = settings.QUERY.replace("$resource", resource_uri)

    sparql = SPARQLWrapper(settings.SPARQL_ENDPOINT)
    sparql.setQuery(query)
    sparql.setReturnFormat(data_format)
    sparql_result = sparql.query().convert()

    # if content_type is not json, send
    if content_type == "application/rdf+xml" or content_type == "text/turtle" or content_type == "application/json":
        return HttpResponse(sparql_result, content_type=content_type)

    def get_labels_for(URI_or_literal, custom_labels):
        """
        returns label_info
        label_info stores URI and their labels or just the labels if it is a literal
        label_info = {
            "labels" = [labels],
            "uri" = URI or None
        }

        it uses the LABEL_URIS defined in settings.py to find labels
        """

        custom_labels = [URIRef(label) for label in custom_labels]
        label_info = {}
        if isinstance(URI_or_literal, URIRef):
            labels = sparql_result.preferredLabel(URI_or_literal,
                                                  labelProperties=custom_labels,
                                                  default=None)
            if labels:
                labels = [label for _, label in labels]
                label_info["labels"] = sorted(labels)
            else:
                label_info["labels"] = [URI_or_literal]

            label_info["uri"] = rewrite_URL(URI_or_literal)

        else:
            label_info["labels"] = [URI_or_literal]
            label_info["uri"] = None

        return label_info

    # create datastructe from RDF data
    quads_by_graph = {}
    for subject_uri, predicate_uri, object_uri, graph in sparql_result.quads():
        if type(graph.identifier) is BNode:
            continue

        graph_key = graph.identifier
        object = None
        is_subject = True
        if subject_uri == resource_uri:
            object = object_uri
        elif object_uri == resource_uri:
            is_subject = False
            object = subject_uri
        else:
            # otherwise it's a label or not directly connected with the resource_uri
            continue

        if graph_key not in quads_by_graph:
            quads_by_graph[graph_key] = {
                "graph_label": get_labels_for(graph.identifier, settings.GRAPH_LABEL_URIS),
                "predicates": {},
            }

        predicate_key = (predicate_uri, is_subject)
        if predicate_key not in quads_by_graph[graph_key]["predicates"]:
            quads_by_graph[graph_key]["predicates"][predicate_key] = {
                "predicate_data": get_labels_for(predicate_uri, settings.LABEL_URIS),
                "is_subject": is_subject,
                "objects": []
            }

        quads_by_graph[graph_key]["predicates"][predicate_key]["objects"].append(get_labels_for(object, settings.LABEL_URIS))

    # sort by graph, predicates and objects so the presentation of the data does not change on a refresh
    quads_by_graph = sorted(quads_by_graph.values(), key=lambda graph: "".join(graph["graph_label"]["labels"]))
    for graph in quads_by_graph:
        graph["predicates"] = sorted(graph["predicates"].values(), key=lambda predicate: predicate["predicate_data"]["labels"])
        for predicate in graph["predicates"]:
            predicate["objects"].sort(key=lambda object: "".join(object["labels"]))
            predicate["num_objects"] = len(predicate["objects"])

    context = {}
    context["resource_label"] = get_labels_for(resource_uri, settings.LABEL_URIS)
    context["resource_uri"] = resource_uri
    context["URI_data"] = quads_by_graph
    context["nsfw_graphs"] = settings.NSFW_GRAPHS
    context["web_base"] = settings.WEB_BASE

    return context


def search(request):
    """
    search function which reads get data from a requests and uses it to find stuff in elasticsearch
    """
    context = {"table": []}
    if "subject" in request.GET or "predicate" in request.GET or "object" in request.GET:
        es = elasticsearch.Elasticsearch(settings.ELASTICSEARCH)

        # create search pattern
        wildcards = []
        wildcards.extend([{"wildcard": {"subject": {"value": f"*{item}*", "case_insensitive": True}}}
                          for item in request.GET["subject"].split()])
        wildcards.extend([{"wildcard": {"predicate": {"value": f"*{item}*", "case_insensitive": True}}}
                          for item in request.GET["predicate"].split()])
        wildcards.extend([{"wildcard": {"object": {"value": f"*{item}*", "case_insensitive": True}}}
                          for item in request.GET["object"].split()])
        search = {"size": 1000,
                  "query":
                  {"bool":
                   {"must": wildcards}}}
        res = es.search(body=search, index="jvmg_search",)

        # change scoring and rewrite_URLs
        new_score_res = []
        for item in res["hits"]["hits"]:
            score = SequenceMatcher(None, item["_source"]["subject"], request.GET["subject"]).ratio()
            score += SequenceMatcher(None, item["_source"]["predicate"], request.GET["predicate"]).ratio()
            score += SequenceMatcher(None, item["_source"]["object"], request.GET["object"]).ratio()

            object_link = None
            if isinstance(item["_source"]["object"], URIRef):
                object_link = rewrite_URL(item["_source"]["object"])
            new_item = {"subject_link": rewrite_URL(item["_source"]["subject"]),
                        "subject": item["_source"]["subject"],
                        "predicate_link": rewrite_URL(item["_source"]["predicate"]),
                        "predicate": item["_source"]["predicate"],
                        "object_link": object_link,
                        "object": item["_source"]["object"],
                        "score": score}
            new_score_res.append(new_item)

        new_score_res.sort(key=lambda item: item["score"], reverse=True)
        context = {"table": new_score_res,
                   "table_len": len(new_score_res),
                   "subject": request.GET["subject"],
                   "predicate": request.GET["predicate"],
                   "object": request.GET["object"]}

    return render(request, "jvmg/search.html", context)


def uri_lookup_ont(request, path):
    """
    creates an ontology table.

    some URIs (like http://mediagraph.link/acdb/ont/) describe a ontology.
    on this pages we need additional information about the ontology.
    this function creates the usual table with information about the URI
    but also creates an additional table with ontology information
    """
    URI = path + "/ont/"
    context = get_data(URI, JSONLD, "text/html")

    URI = settings.DATASET_BASE + URI
    query = f"""
    PREFIX defined: <http://www.w3.org/2000/01/rdf-schema#isDefinedBy>
    PREFIX comment: <http://www.w3.org/2000/01/rdf-schema#comment>
    PREFIX label: <http://www.w3.org/2000/01/rdf-schema#label>

    SELECT ?s ?Property ?comment
    WHERE {{
      ?s defined: <{URI}>.
      ?s comment: ?comment.
      ?s label: ?Property
    }}
    """
    sparql = SPARQLWrapper(settings.SPARQL_ENDPOINT)
    sparql.setQuery(query)
    sparql.setReturnFormat(JSON)
    result = sparql.query().convert()
    ont_table = {"header": result["head"]["vars"], "data": []}
    for item in result["results"]["bindings"]:
        row = []
        for header in ont_table["header"]:
            if item[header]["type"] == "uri":
                row.append(rewrite_URL(item[header]["value"]))
            else:
                row.append(item[header]["value"])
        ont_table["data"].append(row)

    if not ont_table["data"]:
        ont_table = None
    else:
        ont_table["data"].sort(key=lambda item: item[1])

    context["ont_table"] = ont_table

    return render(request, "jvmg/ont_table.html", context)


def uri_crosstab(request):
    """
    gathers data about an URI to create a crosstab.
    """
    resource_uri = URIRef(request.GET["uri"])
    query = f"""PREFIX label: <http://www.w3.org/2000/01/rdf-schema#label>
    SELECT ?value ?v_label ?property ?p_label (count(?value) as ?count)
    WHERE {{
      ?entity ?anything <{resource_uri}> .
      ?entity ?property ?value.
      OPTIONAL {{?value label: ?v_label.}} .
      OPTIONAL {{?property label: ?p_label.}} .
    }} GROUP BY ?value ?v_label ?property ?p_label
    ORDER BY DESC(?count)
    """
    sparql = SPARQLWrapper(settings.SPARQL_ENDPOINT)
    sparql.setQuery(query)
    sparql.setReturnFormat(JSON)
    result = sparql.query().convert()

    trait_count = []
    for entry in result["results"]["bindings"]:
        try:
            property_label = entry["p_label"]["value"]
        except Exception as e:
            property_label = entry["property"]["value"]

        try:
            value_label = entry["v_label"]["value"]
        except Exception as e:
            value_label = entry["value"]["value"]

        trait_count.append({"count": int(entry["count"]["value"]),
                            "value_label": value_label,
                            "property": rewrite_URL(entry["property"]["value"]),
                            "property_label": property_label,
                            "value": rewrite_URL(entry["value"]["value"])})

    context = {}
    context["table"] = trait_count
    context["resource_label"] = resource_uri
    return render(request, "jvmg/count_table.html", context)
