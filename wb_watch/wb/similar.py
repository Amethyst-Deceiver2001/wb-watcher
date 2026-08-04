"""Fetch the "Смотрите также" (see also / similar products) list for an item.

Reverse-engineered from github.com/glmn/wb-private-api (searchSimilarByNm ->
Constants.URLS.SEARCH.SIMILAR_BY_NM = "https://in-similar.wildberries.ru/"),
confirmed live: GET https://in-similar.wildberries.ru/?nm={nm} returns a flat JSON
array of related nm-ids (~140 for a well-trafficked item), no auth needed.

This is the graph-following edge of discovery: WB's own recommendation model links
a dual-use listing to visually/behaviorally similar ones, which is exactly the
neighborhood worth exploring (one grenade tail-fin listing's "see also" tends to be
more tail-fins and drop hardware from adjacent sellers).
"""
from __future__ import annotations

from .. import config, http


def fetch_similar_ids(nm_id: int) -> list[int]:
    data = http.get_json(config.SIMILAR_URL, {"nm": str(nm_id)})
    if not data or not isinstance(data, list):
        return []
    return [int(x) for x in data if isinstance(x, (int, str)) and str(x).isdigit()]
