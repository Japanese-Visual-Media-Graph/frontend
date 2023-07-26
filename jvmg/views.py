from dataclasses import InitVar, dataclass, field
from typing import Tuple, Union
from urllib.error import URLError
from django.shortcuts import render
from django.http import Http404, HttpResponse
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from difflib import SequenceMatcher
from SPARQLWrapper import SPARQLWrapper, XML, JSONLD, TURTLE, JSON
import rdflib
from rdflib import ConjunctiveGraph, Literal, URIRef, BNode
from rdflib.term import Node
from django.conf import settings
from time import perf_counter
import elasticsearch
from elastic_transport import ConnectionError
import logging

logger = logging.getLogger("default")
slow_logger = logging.getLogger("slow")


def rewrite_url(url: URIRef) -> URIRef:
    return URIRef(url.replace(settings.DATASET_BASE, str(settings.WEB_BASE)))


@dataclass
class Info:
    item: Union[Node, rdflib.Graph]
    sparql_result: InitVar[ConjunctiveGraph]
    uri: URIRef = field(init=False)
    labels: list[Literal] = field(init=False)

    def __post_init__(self, sparql_result: ConjunctiveGraph):
        if isinstance(self.item, BNode):
            self.labels = [Literal(str(self.item))]
        elif isinstance(self.item, Literal):
            self.labels = [self.item]

        elif isinstance(self.item, URIRef) or isinstance(self.item, rdflib.Graph):
            if isinstance(self.item, URIRef):
                self.uri = self.item
                label_uris: list[URIRef] = [URIRef(label_uri) for label_uri in settings.LABEL_URIS]
            else:
                self.uri = self.item.identifier
                label_uris: list[URIRef] = [URIRef(label_uri) for label_uri in settings.GRAPH_LABEL_URIS]

            self.labels = []
            for label_uri in label_uris:
                for label in set(sparql_result.objects(subject=self.uri, predicate=label_uri)):
                    if isinstance(label, Literal):
                        self.labels.append(label)
            if self.labels:
                self.labels.sort()
            else:
                self.labels = [Literal(self.uri)]

            self.uri = rewrite_url(self.uri)


@dataclass
class Blank_node:
    uri: BNode
    sparql_result: ConjunctiveGraph
    info: Info = field(init=False)
    items: list[Tuple[Info, list[Info]]] = field(init=False)

    def __post_init__(self):
        self.info = Info(self.uri, self.sparql_result)
        self.items = []

        for predicate in set(self.sparql_result.predicates(subject=self.uri)):
            objects = [
                Info(object, self.sparql_result)
                for object in self.sparql_result.objects(subject=self.uri, predicate=predicate)
            ]
            objects.sort(key=lambda item: "".join(item for item in item.labels))
            self.items.append((Info(predicate, self.sparql_result), objects))

@dataclass
class Predicate:
    graph: rdflib.Graph
    sparql_result: ConjunctiveGraph
    uri: Node
    resource_uri: URIRef
    is_back_link: bool = False
    objects: list[Info] = field(init=False)
    blank_nodes: list[Blank_node] = field(init=False)
    labels: list[Literal] = field(init=False)
    info: Info = field(init=False)
    num_objects: int = field(default=0)
    num_blank_nodes: int = field(default=0)

    def __post_init__(self):
        self.info = Info(self.uri, self.sparql_result)

        if self.is_back_link:
            self.objects = [
                Info(subject, self.sparql_result)
                for subject in set(self.graph.subjects(predicate=self.uri, object=self.resource_uri))
                if not isinstance(subject, BNode)
            ]

            self.blank_nodes = [
                Blank_node(uri=bnode, graph=self.graph, sparql_result=self.sparql_result)
                for bnode in set(self.graph.subjects(predicate=self.uri, object=self.resource_uri))
                if isinstance(bnode, BNode)
            ]
        else:
            self.objects = [
                Info(object, self.sparql_result)
                for object in set(self.graph.objects(predicate=self.uri, subject=self.resource_uri))
                if not isinstance(object, BNode)
            ]

            self.blank_nodes = [
                Blank_node(uri=bnode, graph=self.graph, sparql_result=self.sparql_result)
                for bnode in set(self.graph.objects(predicate=self.uri, subject=self.resource_uri))
                if isinstance(bnode, BNode)
            ]


        self.objects.sort(key=lambda item: "".join(item for item in item.labels))
        self.blank_nodes.sort(key=lambda item: "".join(item for item in item.info.labels))
        self.num_objects = len(self.objects)
        self.num_blank_nodes = len(self.blank_nodes)


@dataclass
class Graph:
    graph: rdflib.Graph
    sparql_result: ConjunctiveGraph
    resource_uri: URIRef
    predicates: list[Predicate] = field(init=False)
    info: Info = field(init=False)

    def __post_init__(self):
        self.info = Info(self.graph, self.sparql_result)

        self.predicates = [
            Predicate(graph=self.graph, sparql_result=self.sparql_result, uri=predicate, resource_uri=self.resource_uri)
            for predicate in set(self.graph.predicates(subject=self.resource_uri))
        ]
        back_links = [
            Predicate(graph=self.graph,
                      sparql_result=self.sparql_result,
                      uri=predicate,
                      is_back_link=True,
                      resource_uri=self.resource_uri)
            for predicate in set(self.graph.predicates(object=self.resource_uri))
        ]
        self.predicates.extend(back_links)
        self.predicates.sort(key=lambda predicate: "".join(label for label in predicate.info.labels))


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
    """
    resource_uri = URIRef(settings.DATASET_BASE + path)
    url_val = URLValidator()
    try:
        url_val(resource_uri)
    except ValidationError:
        logger.warning(f"No data for {resource_uri}<")
        raise Http404
    
    logger.info(f"uri: {resource_uri}")

    # we don't use format() to avoid escaping all the curly braces in sparql queries
    query = settings.QUERY.replace("$resource", resource_uri)

    sparql = SPARQLWrapper(settings.SPARQL_ENDPOINT)
    sparql.setQuery(query)
    sparql.setReturnFormat(data_format)
    start = perf_counter()
    try:
        sparql_result = sparql.query().convert()
    except URLError as e:
        logger.error(f"No connection to sparql endpoint: {settings.SPARQL_ENDPOINT}!")
        raise e    
    
    if len(sparql_result) == 0:
        logger.warning(f"No data for {resource_uri}")
        raise Http404

    query_time = perf_counter() - start
    if query_time > settings.SLOW_LOG_THRESHOLD:
        slow_logger.info(f"uri: {resource_uri} time: {query_time}")    

    # if content_type is not json, send
    if content_type == "application/rdf+xml" or content_type == "text/turtle" or content_type == "application/json":
        return HttpResponse(sparql_result, content_type=content_type)

    graphs = [
        Graph(graph=graph, sparql_result=sparql_result, resource_uri=resource_uri)
        for graph in sparql_result.contexts()
        if not isinstance(graph.identifier, BNode)
    ]
    graphs.sort(key=lambda graph: graph.info.labels)
    
    context = {}
    context["resource_label"] = Info(resource_uri, sparql_result)
    context["resource_uri"] = resource_uri
    context["URI_data"] = graphs
    context["nsfw_graphs"] = settings.NSFW_GRAPHS
    context["web_base"] = settings.WEB_BASE

    return context


def search(request):
    """
    search function which reads get data from a requests and uses it to find stuff in elasticsearch
    """
    context = {"table": []}
    if "subject" in request.GET or "predicate" in request.GET or "object" in request.GET:
        logger.info(f"search terms: {request.GET['subject']} {request.GET['predicate']} {request.GET['object']}")
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
        try:
            res = es.search(body=search, index="jvmg_search",)
        except ConnectionError as e:
            logger.error(f"No connection to elasticsearch: {settings.ELASTICSEARCH}")
            raise e

        # change scoring and rewrite_URLs
        new_score_res = []
        for item in res["hits"]["hits"]:
            score = SequenceMatcher(None, item["_source"]["subject"], request.GET["subject"]).ratio()
            score += SequenceMatcher(None, item["_source"]["predicate"], request.GET["predicate"]).ratio()
            score += SequenceMatcher(None, item["_source"]["object"], request.GET["object"]).ratio()

            object_link = None
            if isinstance(item["_source"]["object"], URIRef):
                object_link = rewrite_url(item["_source"]["object"])
            new_item = {"subject_link": rewrite_url(item["_source"]["subject"]),
                        "subject": item["_source"]["subject"],
                        "predicate_link": rewrite_url(item["_source"]["predicate"]),
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
                row.append(rewrite_url(item[header]["value"]))
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
                            "property": rewrite_url(entry["property"]["value"]),
                            "property_label": property_label,
                            "value": rewrite_url(entry["value"]["value"])})

    context = {}
    context["table"] = trait_count
    context["resource_label"] = resource_uri
    return render(request, "jvmg/count_table.html", context)
