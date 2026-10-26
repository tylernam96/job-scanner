from scanner.sources import (hackernews, himalayas, jobicy, remoteok, remotive, weworkremotely,
                             workingnomads)

# Aggregators: posting age filter applies. Curated ATS boards live in sources/ats.py.
AGGREGATORS = {
    "himalayas": himalayas.fetch,
    "remotive": remotive.fetch,
    "remoteok": remoteok.fetch,
    "jobicy": jobicy.fetch,
    "weworkremotely": weworkremotely.fetch,
    "workingnomads": workingnomads.fetch,
    "hackernews": hackernews.fetch,
}
