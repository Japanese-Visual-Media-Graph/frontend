# JVMG frontend

This is the frontend for our Japanese Visual Media Graph (JVMG)
research project. There we use the Resource Description
Framework (RDF) to create a knowledge graph about Japanese media
like visual novels. This frontend is intended to make that
database easily accessible in the browser.

It is written in Django and the primary job is simple: When the
user looks at an URI it displays all triples which directly link
to this URI as well as their labels. There is an additional
search function to look for tripples with matching text in their
labels (powered by elasticsearch).

The JVMG database integrates many databases from different
communities, each one organized in its own graph. Because of that
the frontend allows you to filter the displayed tripples by graph
in case you're only interested in one data source.


## Dependencies

- Django
- elasticsearch
- Graph database or triplestore (like Fuseki)

## Configuration

All configuration is stored in `setting.py`.

### `SPARQL_ENDPOINT` and `QUERY`

This tells the frontend where to get and how to query the RDF
data. `SPARQL_ENDPOINT` is the URL of the databases SPARQL
endpoint, e.g. to something like Fuseki.

You also have to provide a query, which gathers all the data for a given URI.
The query must contain `$resource`, which will be replaced with the URI you
requested. Below is an example of a SPARQL endpoint and a simple SPARQL query.

```python
SPARQL_ENDPOINT = "http://localhost:3030/jvmg"

QUERY = """
PREFIX label: <http://www.w3.org/2000/01/rdf-schema#label>

CONSTRUCT {
  GRAPH ?graph {
    ?subject ?predicate   ?object .
    ?object    label:   ?objectLabel    .
    ?subject   label:   ?subjectLabel   .
    ?predicate label:   ?predicateLabel .
  } WHERE {
    ?subject ?predicate ?object . FILTER ( ?subject = <$resource> )
    OPTIONAL { ?object    label:  ?objectLabel    }
    OPTIONAL { ?subject   label:  ?subjectLabel   }
    OPTIONAL { ?predicate label:  ?predicateLabel }
}"""
```

### `DATASET_BASE` and `WEB_BASE`

```python
DATASET_BASE = "http://mediagraph.link/"
WEB_BASE = "http://127.0.0.1:8000/"
```

The frontend displays URIs as clickable links. This is nice to
browse or just click your way through the database. But in our
case the URIs in the database (`http://mediagraph.link/`) didn't
match the URL the frontend was actually running
on (`http://127.0.0.1:8000/`). To make it work the frontend
rewrites the URLs in HTML links from `DATASET_BASE` to
`WEB_BASE`. Meaning when the user clicks on a link starting with
`http://mediagraph.link/` the frontend changes that and the
browser actually ends up at our frontend again (instead of going
to `http://mediagraph.link/`).

To be more exact:

- `WEB_BASE`: The domain or IP and port your frontend is running on.
- `DATASET_BASE`: The start of URIs in your database that should be redirected to the frontend.

We needed those config options to make our reverse-proxy configuration work.

### `LABEL_URIS` and `GRAPH_LABEL_URIS`

``` python
LABEL_URIS = ["http://www.w3.org/2000/01/rdf-schema#label"]
GRAPH_LABEL_URIS = ["http://mediagraph.link/jvmg/ont/shortLabel"]
```

The frontend fetches all data via the SPARQL query in `QUERY`.
Every time the frontend shows an URI it tries to find a matching
label in the returned tripples. That way the data is way more
readable and explorable.

Note that the SPARQL query has to fetch and return those tripples
by itself. The config options here just tells the frontend how to
find the labels for each URI.

`LABEL_URIS` is a list of URIs that are used to identify labels for a given URI.
`GRAPH_LABEL_URIS` is the same but used to find the names of graphs. This is a special configuration option because we needed different label URIs for them.

### `NSFW_GRAPHS`

We also included the possibility to hide data from certain graphs
by default. `NSFW_GRAPHS` is a list of graph-URIs which should be
hidden unless the user decides otherwise. Be aware that this
happens via JavaScript. The browser gets the data anyway, it is
just not shown automatically.

``` python
NSFW_GRAPHS = ["http://mediagraph.link/graph/vndb_nsfw"]
```
### `ELASTICSEARCH_IP` and `ELASTICSEARCH_PORT`

The frontend needs to know where your elasticsearch is running,
in case you want to search for something. Look [here](https://github.com/Japanese-Visual-Media-Graph/utils),
in order to create an elasticsearch index for your data.

``` python
ELASTICSEARCH = "http://127.0.0.1:9200"
```
