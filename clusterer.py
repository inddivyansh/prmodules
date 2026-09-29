from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer


def cluster_posts(posts: list[dict[str, Any]], max_clusters: int = 4) -> dict[str, Any]:
    """Group posts and extracted claims into topical narrative clusters using TF-IDF and K-Means.

    Returns:
        A dictionary containing:
        - "clusters": List of cluster summaries (id, label, top_keywords, count, sentiment_breakdown)
        - "posts": The original post items with added 'cluster_id' and 'cluster_label' fields.
    """
    if not posts:
        return {"clusters": [], "posts": []}

    # Prepare document texts: combine captions and any extracted claims
    docs = []
    for p in posts:
        caption = p.get("caption") or (p.get("post") or {}).get("caption", "")
        claims = p.get("claims") or (p.get("analysis") or {}).get("claims", [])
        combined = f"{caption} {' '.join(claims)}".strip()
        docs.append(combined if combined else "empty post")

    n_samples = len(docs)

    # Edge case: Very small number of posts
    if n_samples < 2:
        posts[0]["cluster_id"] = 0
        posts[0]["cluster_label"] = "Cluster 1: General Social Discourse"
        return {
            "clusters": [
                {
                    "id": 0,
                    "label": "Cluster 1: General Social Discourse",
                    "keywords": ["general", "post"],
                    "count": 1,
                    "sentiment_breakdown": {
                        (posts[0].get("analysis") or {}).get("sentiment", "NEUTRAL"): 1
                    },
                }
            ],
            "posts": posts,
        }

    # Vectorize documents
    k = min(n_samples, max(2, min(n_samples // 2, max_clusters)))
    try:
        vectorizer = TfidfVectorizer(
            stop_words="english",
            max_features=100,
            ngram_range=(1, 2),
        )
        tfidf_matrix = vectorizer.fit_transform(docs)
        feature_names = vectorizer.get_feature_names_out()

        kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
        cluster_labels = kmeans.fit_predict(tfidf_matrix)

        # Extract top keywords for each centroid
        cluster_summaries = []
        for i in range(k):
            centroid = kmeans.cluster_centers_[i]
            top_indices = centroid.argsort()[::-1][:5]
            top_words = [feature_names[idx] for idx in top_indices if centroid[idx] > 0]
            if not top_words:
                top_words = [f"narrative-{i+1}"]

            label = f"Cluster {i+1}: {', '.join(top_words[:3]).title()}"

            # Calculate stats for this cluster
            matching_posts = [p for idx, p in enumerate(posts) if cluster_labels[idx] == i]
            sentiments = [
                (p.get("analysis") or {}).get("sentiment", "NEUTRAL")
                for p in matching_posts
            ]
            sent_counts = dict(Counter(sentiments))

            cluster_summaries.append({
                "id": i,
                "label": label,
                "keywords": top_words,
                "count": len(matching_posts),
                "sentiment_breakdown": sent_counts,
            })

        # Attach cluster info back to posts
        for idx, p in enumerate(posts):
            cid = int(cluster_labels[idx])
            p["cluster_id"] = cid
            p["cluster_label"] = cluster_summaries[cid]["label"]

        return {
            "clusters": cluster_summaries,
            "posts": posts,
        }

    except Exception as exc:
        logging.warning("Clustering encountered fallback: %s", exc)
        for p in posts:
            p["cluster_id"] = 0
            p["cluster_label"] = "Cluster 1: Monitored Posts"
        return {
            "clusters": [
                {
                    "id": 0,
                    "label": "Cluster 1: Monitored Posts",
                    "keywords": ["all", "posts"],
                    "count": len(posts),
                    "sentiment_breakdown": {},
                }
            ],
            "posts": posts,
        }
