"""The Cypher the product depends on. Each function returns rows plus the query text, so the UI can show both."""

from .graph import graph

# For a drug, every same-class substitute and the plants it shares with the original.
TRAP = """
MATCH (i:Ingredient {name: $drug})-[:IN_CLASS]->(c:Class)<-[:IN_CLASS]-(alt:Ingredient)
WHERE alt <> i
OPTIONAL MATCH (i)<-[:CONTAINS]-(p:Product)<-[:MAKES]-(f:Facility)-[:MAKES]->(q:Product)-[:CONTAINS]->(alt)
OPTIONAL MATCH (f)-[:FLAGGED]->(a:Action)
WITH alt, c, f, collect(DISTINCT a.type + ' ' + a.date) AS flags,
     count(DISTINCT p) AS orig_products, count(DISTINCT q) AS alt_products
RETURN alt.name AS substitute, c.name AS class,
       collect(CASE WHEN f IS NULL THEN NULL ELSE {plant: f.name, fei: f.fei, country: f.country,
               flags: flags, orig_products: orig_products, alt_products: alt_products} END) AS shared_plants
ORDER BY size(shared_plants) DESC, substitute
"""

# Everything that depends on one plant, and which of it is already short.
PLANT_DOWN = """
MATCH (f:Facility {fei: $fei})-[:MAKES]->(p:Product)-[:CONTAINS]->(i:Ingredient)
OPTIONAL MATCH (p)-[:IN_SHORTAGE]->(s:Shortage {status: 'Current'})
OPTIONAL MATCH (i)<-[:CONTAINS]-(other:Product)
WITH f, i, count(DISTINCT p) AS from_plant, count(DISTINCT other) AS all_products, count(DISTINCT s) AS short_now
RETURN f.name AS plant, i.name AS ingredient, from_plant, all_products,
       round(100.0 * from_plant / all_products) AS share_pct, short_now
ORDER BY share_pct DESC, from_plant DESC
"""

# Plants ranked by how much of each ingredient's US label count runs through them, for ingredients now in shortage.
CHOKEPOINTS = """
MATCH (i:Ingredient)<-[:CONTAINS]-(:Product)-[:IN_SHORTAGE]->(:Shortage {status: 'Current'})
WITH DISTINCT i
MATCH (i)<-[:CONTAINS]-(p:Product)
WITH i, count(DISTINCT p) AS total
MATCH (i)<-[:CONTAINS]-(p:Product)<-[:MAKES]-(f:Facility)
WITH f, i, total, count(DISTINCT p) AS n
WHERE total >= 3
WITH f, collect({ingredient: i.name, share: round(100.0 * n / total)}) AS deps,
     sum(toFloat(n) / total) AS exposure
OPTIONAL MATCH (f)-[:FLAGGED]->(a:Action)
RETURN f.name AS plant, f.fei AS fei, f.country AS country, round(100 * exposure) / 100 AS exposure,
       size(deps) AS ingredients, deps AS top, collect(DISTINCT a.date) AS import_alert_dates
ORDER BY exposure DESC
LIMIT $limit
"""

# Plants that bridge the most drug supply chains: betweenness centrality on the plant-drug projection.
BRIDGES = """
CALL algo.betweenness({nodeLabels: ['Facility', 'Ingredient'], relationshipTypes: ['SUPPLIES', 'DEPENDS_ON'],
                       samplingSize: $nodes, samplingSeed: 7})
YIELD node, score
WHERE node:Facility AND score > 0
WITH node AS f, score ORDER BY score DESC LIMIT $limit
OPTIONAL MATCH (f)-[:SUPPLIES]->(i:Ingredient)
OPTIONAL MATCH (f)-[:FLAGGED]->(a:Action)
WITH f, score, collect(DISTINCT i.name) AS drugs, collect(DISTINCT a.date) AS import_alert_dates
OPTIONAL MATCH (f)-[:SUPPLIES]->(:Ingredient)-[:IN_CLASS]->(c:Class)
RETURN f.name AS plant, f.fei AS fei, f.country AS country, round(score) AS betweenness,
       size(drugs) AS drugs_made, count(DISTINCT c) AS classes, drugs[0..6] AS sample, import_alert_dates
ORDER BY betweenness DESC
"""

# Same class, no plant shared with the original, no flagged plant, not itself short.
SAFE = """
MATCH (i:Ingredient {name: $drug})-[:IN_CLASS]->(:Class)<-[:IN_CLASS]-(alt:Ingredient)
WHERE alt <> i
OPTIONAL MATCH (i)<-[:CONTAINS]-(:Product)<-[:MAKES]-(shared:Facility)-[:MAKES]->(:Product)-[:CONTAINS]->(alt)
WITH i, alt, count(DISTINCT shared) AS shared_plants
WHERE shared_plants = 0
MATCH (alt)<-[:CONTAINS]-(q:Product)
// The FLAGGED hop sits in WHERE: FalkorDB 4.22 drops it from a two-hop OPTIONAL MATCH and counts every plant.
OPTIONAL MATCH (q)<-[:MAKES]-(bad:Facility) WHERE (bad)-[:FLAGGED]->(:Action)
OPTIONAL MATCH (q)-[:IN_SHORTAGE]->(s:Shortage {status: 'Current'})
WITH alt, count(DISTINCT q) AS labels, count(DISTINCT bad) AS flagged_plants, count(DISTINCT s) AS short_now
RETURN alt.name AS substitute, labels, flagged_plants, short_now
ORDER BY short_now, flagged_plants, labels DESC
"""


# Every plant that manufactures the drug, with any FDA action against it.
DRUG_PLANTS = """
MATCH (i:Ingredient {name: $drug})<-[:CONTAINS]-(p:Product)<-[:MAKES]-(f:Facility)
OPTIONAL MATCH (f)-[:FLAGGED]->(a:Action)
RETURN f.name AS plant, f.fei AS fei, f.country AS country, count(DISTINCT p) AS products,
       collect(DISTINCT a.date) AS import_alert_dates
ORDER BY products DESC, plant
"""


def run(cypher: str, **params) -> list[dict]:
    res = graph().ro_query(cypher, params)
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in res.header]
    return [dict(zip(cols, row, strict=True)) for row in res.result_set]


def trap(drug: str) -> list[dict]:
    return run(TRAP, drug=drug.upper())


def plant_down(fei: str) -> list[dict]:
    return run(PLANT_DOWN, fei=fei)


def chokepoints(limit: int = 15) -> list[dict]:
    return run(CHOKEPOINTS, limit=limit)


def drug_plants(drug: str) -> list[dict]:
    return run(DRUG_PLANTS, drug=drug.upper())


def bridges(limit: int = 15) -> list[dict]:
    # algo.betweenness samples 32 source nodes by default; sampling every node gives the exact score.
    nodes = run("MATCH (n) WHERE n:Facility OR n:Ingredient RETURN count(n) AS n")[0]["n"]
    return run(BRIDGES, limit=limit, nodes=nodes)


def safe(drug: str) -> list[dict]:
    return run(SAFE, drug=drug.upper())
