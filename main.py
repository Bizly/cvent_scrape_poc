import logging
from uuid import uuid4

import pandas as pd

from services.scraper import CventScraper
from services.logging_setup import configure_logging, log_context
from services.queues import (
    enqueue_url,
    pop_url,
    push_dlq_destination,
    push_dlq_url,
    cache_push_result,
    cache_flush_if_ready,
    cache_final_drain,
    queue_len,
    dlq_counts,
)
from config import DEBUG_MODE, DEBUG_LIMIT, INPUT_CSV, BATCH_SIZE

configure_logging()
logger = logging.getLogger(__name__)


def _short_trace() -> str:
    return uuid4().hex[:8]


def run_phase1(scraper: CventScraper) -> int:
    """
    Phase 1 — Link Discovery.
    For each destination, scrape venue URLs and RPUSH them onto the Redis
    work queue. Deduplicates across destinations using an in-memory set so
    the same link is not enqueued twice in a single run.
    On destination-level failure (after throttled_get's HTTP retries
    exhaust), push the destination to the DLQ and continue.
    """
    df = pd.read_csv(INPUT_CSV)
    destinations = df[["country", "city_name"]].drop_duplicates()

    logger.info("PHASE1_START destinations=%d", len(destinations))

    seen: set[str] = set()
    enqueued = 0

    for _, row in destinations.iterrows():
        country, city = row["country"], row["city_name"]
        trace_id = _short_trace()
        dest_label = f"{city},{country}"

        with log_context(trace_id=trace_id, dest=dest_label):
            logger.info("DEST_START")
            try:
                links = scraper.scrape_venue_links(country, city)
            except Exception as e:
                logger.exception("DEST_FAIL_DLQ error=%s", e)
                push_dlq_destination(country, city, trace_id, str(e))
                continue

            new_count = 0
            for link in links:
                if link in seen:
                    continue
                seen.add(link)
                enqueue_url(link, country, city, trace_id)
                enqueued += 1
                new_count += 1

                if DEBUG_MODE and enqueued >= DEBUG_LIMIT:
                    break

            logger.info("DEST_SUCCESS urls=%d new=%d", len(links), new_count)

        if DEBUG_MODE and enqueued >= DEBUG_LIMIT:
            logger.info("PHASE1_DEBUG_CAP reached=%d limit=%d", enqueued, DEBUG_LIMIT)
            break

    counts = dlq_counts()
    logger.info(
        "PHASE1_END enqueued=%d queue_len=%d dlq_destinations=%d",
        enqueued,
        queue_len(),
        counts["dlq_destinations"],
    )
    return enqueued


def run_phase2(scraper: CventScraper) -> int:
    """
    Phase 2 — Drain the URL queue serially, scrape each, cache to Redis,
    and flush the cache to the Bizly API every BATCH_SIZE successful scrapes.
    On per-URL failure, push to the DLQ and continue to the next URL.
    """
    start_len = queue_len()
    logger.info("PHASE2_START queue_len=%d", start_len)

    scraped = 0
    flushed = 0

    while True:
        job = pop_url()
        if job is None:
            break

        link = job.get("link")
        country = job.get("country")
        city = job.get("city")
        trace_id = job.get("trace_id") or _short_trace()
        dest_label = f"{city},{country}"

        with log_context(trace_id=trace_id, dest=dest_label):
            logger.info("URL_START link=%s", link)
            try:
                details = scraper.scrape_venue_details(link, country, city)
            except Exception as e:
                logger.exception("URL_FAIL_DLQ link=%s error=%s", link, e)
                push_dlq_url(link, country, city, trace_id, str(e))
                continue

            cache_push_result(details)
            scraped += 1
            logger.info("URL_SUCCESS link=%s", link)

            flushed += cache_flush_if_ready(BATCH_SIZE)

    flushed += cache_final_drain()

    counts = dlq_counts()
    logger.info(
        "PHASE2_END scraped=%d flushed=%d dlq_urls=%d",
        scraped,
        flushed,
        counts["dlq_urls"],
    )
    return scraped


def run() -> None:
    scraper = CventScraper()
    run_phase1(scraper)
    run_phase2(scraper)

    counts = dlq_counts()
    logger.info(
        "RUN_SUMMARY queue_len=%d dlq_destinations=%d dlq_urls=%d",
        queue_len(),
        counts["dlq_destinations"],
        counts["dlq_urls"],
    )


if __name__ == "__main__":
    run()
