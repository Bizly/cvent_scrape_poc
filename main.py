import logging
from uuid import uuid4

import pandas as pd

from services.scraper import CventScraper
from services.logging_setup import configure_logging, log_context
from services.queues import (
    enqueue_destination,
    peek_destination,
    ack_destination,
    destination_queue_len,
    enqueue_url_if_unseen,
    clear_seen_urls,
    pop_url,
    push_dlq_destination,
    push_dlq_url,
    cache_push_result,
    cache_flush_if_ready,
    cache_final_drain,
    queue_len,
    dlq_counts,
    is_destination_processed,
    mark_destination_processed,
)
from config import DEBUG_MODE, DEBUG_LIMIT, INPUT_CSV, BATCH_SIZE

configure_logging()
logger = logging.getLogger(__name__)


def _short_trace() -> str:
    return uuid4().hex[:8]


def run_phase0() -> int:
    """
    Phase 0 — Destination Ingestion.
    Read the CSV, create a destination entry for every unique city/country pair,
    and RPUSH each one onto the Redis destination queue.
    Also clears the seen-URL set so a fresh run re-discovers all venues.
    """
    df = pd.read_csv(INPUT_CSV)
    destinations = df[["country", "city_name"]].drop_duplicates()

    clear_seen_urls()

    logger.info("PHASE0_START destinations=%d", len(destinations))

    count = 0
    for _, row in destinations.iterrows():
        country, city = row["country"], row["city_name"]
        trace_id = _short_trace()
        enqueue_destination(country, city, trace_id)
        count += 1

        if DEBUG_MODE and count >= DEBUG_LIMIT:
            logger.info("PHASE0_DEBUG_CAP reached=%d limit=%d", count, DEBUG_LIMIT)
            break

    logger.info("PHASE0_END queued=%d dest_queue_len=%d", count, destination_queue_len())
    return count


def run_phase1(scraper: CventScraper) -> int:
    """
    Phase 1 — Link Discovery.
    Drain the destination queue: peek at the head entry, scrape its venue URLs,
    push new (unseen) ones onto the URL queue, then acknowledge (remove) the
    destination once link discovery is complete.

    On destination-level failure the destination is sent to the DLQ and
    acknowledged so the queue keeps moving.
    """
    start_len = destination_queue_len()
    logger.info("PHASE1_START dest_queue_len=%d", start_len)

    enqueued = 0

    while True:
        dest = peek_destination()
        if dest is None:
            break

        country = dest.get("country")
        city = dest.get("city")
        trace_id = dest.get("trace_id") or _short_trace()
        dest_label = f"{city},{country}"

        with log_context(trace_id=trace_id, dest=dest_label):
            if is_destination_processed(country, city):
                logger.info("DEST_SKIP (already processed)")
                continue

            logger.info("DEST_START")
            try:
                links = scraper.scrape_venue_links(country, city)
                mark_destination_processed(country, city)
            except Exception as e:
                logger.exception("DEST_FAIL_DLQ error=%s", e)
                push_dlq_destination(country, city, trace_id, str(e))
                ack_destination()
                continue

            new_count = 0
            skipped_redis = 0
            for link in links:
                if enqueue_url_if_unseen(link, country, city, trace_id):
                    enqueued += 1
                    new_count += 1

                if DEBUG_MODE and enqueued >= DEBUG_LIMIT:
                    break

            # Remove the destination from the queue only after link discovery
            # is fully done and all found URLs have been enqueued.
            ack_destination()
            logger.info("DEST_SUCCESS urls=%d new=%d", len(links), new_count)

        if DEBUG_MODE and enqueued >= DEBUG_LIMIT:
            logger.info("PHASE1_DEBUG_CAP reached=%d limit=%d", enqueued, DEBUG_LIMIT)
            break

    counts = dlq_counts()
    logger.info(
        "PHASE1_END enqueued=%d url_queue_len=%d dlq_destinations=%d",
        enqueued,
        queue_len(),
        counts["dlq_destinations"],
    )
    return enqueued


def run_phase2(scraper: CventScraper) -> int:
    """
    Phase 2 — Venue Detail Scraping.
    Drain the URL queue serially, scrape each venue, cache the result in Redis,
    and flush to the Bizly API every BATCH_SIZE successful scrapes.
    On per-URL failure, push to the DLQ and continue.
    """
    start_len = queue_len()
    logger.info("PHASE2_START url_queue_len=%d", start_len)

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

    # ------------------------------------------------------------------ #
    # Resume: if the scraper went down mid-run, finish outstanding work   #
    # before touching the CSV again.                                      #
    # ------------------------------------------------------------------ #

    url_backlog = queue_len()
    dest_backlog = destination_queue_len()

    if url_backlog > 0:
        logger.info(
            "RESUME url_backlog=%d — draining venue URL queue before fresh run",
            url_backlog,
        )
        run_phase2(scraper)

    if dest_backlog > 0:
        logger.info(
            "RESUME dest_backlog=%d — finishing link discovery before fresh run",
            dest_backlog,
        )
        run_phase1(scraper)
        run_phase2(scraper)

    # ------------------------------------------------------------------ #
    # Fresh run: ingest CSV → discover links → scrape venues              #
    # ------------------------------------------------------------------ #

    run_phase0()
    run_phase1(scraper)

    # 3. Drain any newly discovered links
    run_phase2(scraper)

    counts = dlq_counts()
    logger.info(
        "RUN_SUMMARY dest_queue=%d url_queue=%d dlq_destinations=%d dlq_urls=%d",
        counts["queue_destinations"],
        counts["queue_urls"],
        counts["dlq_destinations"],
        counts["dlq_urls"],
    )


if __name__ == "__main__":
    run()
