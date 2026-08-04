"""Fetch product reviews from feedbacks{1,2}.wb.ru.

Reviews are keyed on imt_id (the product `root`), not nm_id. Two mirror hosts exist;
try host 1, fall back to host 2. Returns normalized review records plus the aggregate
rating distribution. WB caps the response at ~250 most-relevant reviews (no pagination
on this endpoint), so total captured grows over repeated runs as new reviews appear.
"""
from __future__ import annotations

from typing import Any

from .. import config, http


def _normalize(fb: dict[str, Any], nm_id: int, imt_id: int) -> dict[str, Any]:
    photos = fb.get("photos") or fb.get("photo") or []
    votes = fb.get("votes") or {}
    answer = fb.get("answer") or {}
    return {
        "id": fb.get("id"),
        "nm_id": fb.get("nmId") or nm_id,
        "imt_id": imt_id,
        "wb_user_country": (fb.get("wbUserDetails") or {}).get("country"),
        "text": fb.get("text"),
        "pros": fb.get("pros"),
        "cons": fb.get("cons"),
        "valuation": fb.get("productValuation"),
        "created_date": fb.get("createdDate"),
        "has_photo": 1 if photos else 0,
        "has_video": 1 if fb.get("video") else 0,
        "votes_plus": votes.get("pluses"),
        "votes_minus": votes.get("minuses"),
        "seller_answer": answer.get("text") if isinstance(answer, dict) else None,
    }


def fetch_reviews(nm_id: int, imt_id: int) -> dict[str, Any] | None:
    """Return {'reviews': [...], 'distribution': {...}, 'feedback_count': N} or None."""
    data = None
    for host_n in (1, 2):
        url = config.FEEDBACKS_URL.format(n=host_n, imt_id=imt_id)
        data = http.get_json(url)
        if data and (data.get("feedbacks") is not None):
            break
    if not data:
        return None

    feedbacks = data.get("feedbacks") or []
    reviews = [_normalize(fb, nm_id, imt_id) for fb in feedbacks if fb.get("id")]
    return {
        "reviews": reviews,
        "distribution": data.get("valuationDistribution"),
        "feedback_count": data.get("feedbackCount"),
        "feedback_count_with_text": data.get("feedbackCountWithText"),
        "feedback_count_with_photo": data.get("feedbackCountWithPhoto"),
    }
