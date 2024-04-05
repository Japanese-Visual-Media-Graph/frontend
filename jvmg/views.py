from dataclasses import InitVar, dataclass, field
from io import StringIO
from typing import Tuple, Union
from urllib.error import URLError
from django.http.response import JsonResponse
from django.shortcuts import render
from django.http import Http404, HttpResponse
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from django.views.decorators.csrf import csrf_exempt
from SPARQLWrapper import SPARQLWrapper, XML, JSONLD, TURTLE, JSON, CSV
import rdflib
from rdflib import ConjunctiveGraph, Literal, URIRef, BNode
from rdflib.term import Node
from django.conf import settings
from time import perf_counter
import elasticsearch
import logging
import json
from csv import DictReader

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
                    if isinstance(label, Literal) and len(label):
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
                Blank_node(uri=bnode, sparql_result=self.sparql_result)
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
                Blank_node(uri=bnode, sparql_result=self.sparql_result)
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


def main(request, path, query=None):
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
    if not query:
        query = settings.QUERY

    for accepted_format in accepted_formats:
        if accepted_format == "application/rdf+xml":
            return get_data(path, XML, "application/rdf+xml", query)
        elif accepted_format == "text/turtle":
            return get_data(path, TURTLE, "text/turtle", query)
        elif accepted_format == "application/json":
            return get_data(path, JSONLD, "application/json", query)
        else:
            context = get_data(path, JSONLD, "text/html", query)
            return render(request, "jvmg/main.html", context)


def get_data(path, data_format, content_type, query):
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
    query = query.replace("$resource", resource_uri)

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

    return context


def get_cluster(request, path):
    """returns a matched rdf cluster"""
    return main(request, f"jvmg/{path}", query=settings.QUERY_CLUSTER)


def get_label_for(uri, res):
    LABEL = URIRef("http://www.w3.org/2000/01/rdf-schema#label")
    label = "".join([str(label) for label in res.objects(subject=uri, predicate=LABEL)])
    if len(label) == 0:
        return str(uri)
    else:
        return label


def search(request):
    context = {}
    if "search" in request.GET:
        es = elasticsearch.Elasticsearch(settings.ELASTICSEARCH)

        checked = []
        search_type = "match"
        print(request.GET)
        for key in request.GET:
            if key == "search":
                continue
            if key == "btn" and request.GET["btn"] == "exact":
                search_type = "match_phrase"
                continue
            checked.extend(request.GET.getlist(key))

        facets = {"type": {"terms": {"field": "type", "size": 10000}},
                  "graph": {"terms": {"field": "graph", "size": 10000}}}

        facet_res = es.search(index=settings.SEARCH_INDEX,
                              query={search_type: {"label": request.GET["search"]}},
                              aggregations=facets,
                              size=0)

        context = {
            "search": request.GET["search"],
            "total": facet_res["hits"]["total"]["value"],
            "aggs": facet_res["aggregations"],
            "checked": checked,
            "search_type": search_type
        }
    return render(request, "jvmg/search.html", context)


@csrf_exempt
def get_search_page(request):
    if request.method != "POST":
        return JsonResponse({"error": "invalid request"}, status=404)
    else:
        body = json.loads(request.body)
        es = elasticsearch.Elasticsearch(settings.ELASTICSEARCH)

        search_filter = []
        search_type = body["search_type"]
        for key, items in body["checkboxes"].items():
            search_filter.append({"terms": {key: items}})

        if search_filter:
            query = {"bool":
                     {"must":
                      [
                          {search_type: {"label": body["search"]}}
                      ],
                      "filter": [{"bool": {"must": search_filter}}]
                      }}
        else:
            query = {"bool":
                     {"must":
                      [
                          {search_type: {"label": body["search"]}}
                      ]}}

        facets = {"type": {"terms": {"field": "type", "size": 10000}},
                  "graph": {"terms": {"field": "graph", "size": 10000}}}

        search_res = es.search(query=query,
                               index=settings.SEARCH_INDEX,
                               highlight={"fields": {"label" : { "pre_tags" : ["<mark>"], "post_tags" : ["</mark>"] }}},
                               from_=settings.ELASTICSEARCH_PAGE_SIZE * int(body["page"]),
                               size=settings.ELASTICSEARCH_PAGE_SIZE,
                               aggregations=facets)

        if search_res["hits"]["total"]["value"] == 0:
            return JsonResponse({"error": "no data found"}, status=404)

        hits = []
        for entry in search_res["hits"]["hits"]:
            for key in entry["_source"]:
                if key in entry["highlight"]:
                    entry["_source"][key] = entry["highlight"][key]

            hits.append(entry["_source"])

        facets = {}
        for key in search_res["aggregations"]:
            if key not in facets:
                facets[key] = {}

            for item in search_res["aggregations"][key]["buckets"]:
                if item["key"] not in facets[key]:
                    facets[key][item["key"]] = {}

                facets[key][item["key"]] = item["doc_count"]

        return JsonResponse({
            "hits": hits,
            "facets": facets
        })


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


def overview(request):
    sparql = SPARQLWrapper(settings.SPARQL_ENDPOINT)
    sparql.setQuery(settings.QUERY_OVERVIEW)
    sparql.setReturnFormat(CSV)
    result = sparql.query().convert()
    data = list(DictReader(StringIO(result.decode("utf-8"))))
    group_by_graph = {}
    for item in data:
        if not item["graph_label"]:
            item["graph_label"] = "<no graph found>"
        if item["graph"] not in group_by_graph:
            group_by_graph[item["graph"]] = []
        group_by_graph[item["graph"]].append(item)
        item["order"] = int(item["order"])
        item["count"] = int(item["count"])

    for item in group_by_graph.values():
        item.sort(key=lambda i: i["order"])

    return render(request, "jvmg/overview.html", context={"sources": group_by_graph.values()})
