# GLAMhack 2026
> TL;DR Project scope is to enrich a dataset – containing 23 338 places – with geospatial metadata.

The Burgerbibliothek Bern uses descriptors (index terms) for the description of their descriptive units. One of the commonly applied descriptor types is “places” of which there are currently 23 338 recorded. 

The description of places in archives is more complex than simple point geocoding, because the relevant geography is often temporal, and contextual (e.g. addresses and structures cease to exist, and/or are renamed, borders change, etc.). A rare but interesting case: In Aarau (AG) an entire building, which nowadays is the home to the city's library, was moved 54 meters (link).

The application of geospatial metadata in archives facilitates the possibility of new visualizations and maybe the gain of new insights.

## Pains
- The place descriptors lack geospatial coordinates.
- The place descriptors are not differentiated, sometimes a place descriptor describes a point (e. g. Bernastrasse 15) sometimes a zone (e. g. Bern).
- A challenge remains how to describe places in a historical manner
- Most place descriptors have no persistent ID, which makes is not ideal for interoperability.

## Goals
- Add geospatial data to the existing descriptors (e.g. by from wikidata.org or openstreetmap.org).
- Visualize descriptive units on a map.
- Documentation of Challenges.

## Challenge owners
<div>Nadja Ackermann ؜؜؜؜؜؜(nadja<span>­.</span>ackermann<span>@­</span>burgerbib<span>.</span>ch), Burgerbibliothek Bern</div> 
<div>Nadja Glarner (<span>nadja. ­</span>glarner<span>@</span>­burgerbib<span>.ch</span>), Burgerbibliothek Bern</div>
<div>Gionathan Diani (<span>gionathan­</span>.­<span>diani</span><span>@</span>burger­bib.<span>ch</span>), Burgerbibliothek Bern</div>

# Organzation of repository
This repository is organized as a monorepo.
- The folder `data` contains the datasets.
