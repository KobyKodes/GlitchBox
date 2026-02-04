#!/usr/bin/env python3
"""
Bingeflix M3U8 Scraper - Standalone Playwright script
Navigates bingeflix.tv to intercept m3u8 stream URLs.

Usage:
    python3 bingeflix_scraper.py <tmdb_id> <type> [season] [episode]

Examples:
    python3 bingeflix_scraper.py 550 movie          # Fight Club
    python3 bingeflix_scraper.py 1396 tv 1 1         # Breaking Bad S01E01

Output (stdout): JSON object with {success, hls_url, subtitles, referer, error}
All debug logging goes to stderr.
"""

import sys
import json
import time
import re

def log(msg):
    """Print debug info to stderr (stdout reserved for JSON output)."""
    print(f"[BingeflixScraper] {msg}", file=sys.stderr, flush=True)

def scrape(tmdb_id, content_type='movie', season=None, episode=None):
    from playwright.sync_api import sync_playwright

    # Collect ALL m3u8 responses - the player may try multiple servers
    m3u8_responses = []  # [{url, content, referer, status}]
    subtitles = []

    def handle_response(response):
        url = response.url
        if '.m3u8' not in url:
            return

        status = response.status
        log(f"M3U8 response (status {status}): {url}")

        entry = {'url': url, 'content': None, 'referer': None, 'status': status}

        # Try to capture response body (only works for non-redirect responses)
        if status >= 200 and status < 300:
            try:
                body = response.text()
                if body and '#EXTM3U' in body:
                    entry['content'] = body
                    log(f"Captured valid m3u8 content ({len(body)} bytes)")
                else:
                    log(f"Response body is not a valid m3u8")
            except Exception as e:
                log(f"Could not read body: {e}")

        # Extract referer from the original request in the redirect chain
        try:
            req = response.request
            while req.redirected_from:
                req = req.redirected_from
            entry['referer'] = req.headers.get('referer', '')
        except Exception:
            pass

        m3u8_responses.append(entry)

    def handle_response_subtitles(response):
        url = response.url
        # Capture subtitle files
        if any(ext in url for ext in ['.vtt', '.srt']):
            log(f"Intercepted subtitle: {url}")
            lang = 'unknown'
            lang_match = re.search(r'[/.](\w{2,3})\.(vtt|srt)', url)
            if lang_match:
                lang = lang_match.group(1)
            subtitles.append({
                'url': url,
                'lang': lang,
                'format': 'vtt' if '.vtt' in url else 'srt'
            })

    # Build the target URL
    if content_type == 'movie':
        target_url = f"https://bingeflix.tv/movie/{tmdb_id}"
    elif content_type == 'tv' and season and episode:
        target_url = f"https://bingeflix.tv/tv/{tmdb_id}/{season}/{episode}"
    elif content_type == 'tv':
        target_url = f"https://bingeflix.tv/tv/{tmdb_id}"
    else:
        return {'success': False, 'error': f'Invalid content type: {content_type}'}

    log(f"Target URL: {target_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-software-rasterizer',
                '--disable-extensions',
                '--disable-background-networking',
                '--single-process',
            ]
        )

        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 720},
            ignore_https_errors=True,
        )

        page = context.new_page()

        # Register response listeners
        page.on('response', handle_response)
        page.on('response', handle_response_subtitles)

        play_selectors = [
            'button[aria-label="Play"]',
            '.play-button',
            '.btn-play',
            '[class*="play"]',
            'button:has(svg)',
            '.jw-icon-playback',
            '#player',
            '.player-wrapper',
            'video',
        ]

        def has_good_m3u8():
            """Check if we have an m3u8 with actual content (not just a URL)."""
            return any(r['content'] for r in m3u8_responses)

        try:
            log("Navigating to page...")
            page.goto(target_url, wait_until='domcontentloaded', timeout=30000)
            log("Page loaded, waiting for m3u8...")

            # Wait for initial network activity
            page.wait_for_timeout(3000)

            # Try clicking play buttons
            if not m3u8_responses:
                log("No m3u8 yet, looking for play buttons...")
                for selector in play_selectors:
                    try:
                        el = page.query_selector(selector)
                        if el and el.is_visible():
                            log(f"Clicking: {selector}")
                            el.click()
                            page.wait_for_timeout(2000)
                            if m3u8_responses:
                                break
                    except Exception:
                        continue

            # Try iframes
            if not m3u8_responses:
                log("Checking iframes for player...")
                frames = page.frames
                for frame in frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        log(f"Checking frame: {frame.url}")
                        for selector in play_selectors:
                            try:
                                el = frame.query_selector(selector)
                                if el and el.is_visible():
                                    log(f"Clicking in frame: {selector}")
                                    el.click()
                                    page.wait_for_timeout(2000)
                                    if m3u8_responses:
                                        break
                            except Exception:
                                continue
                        if m3u8_responses:
                            break
                    except Exception:
                        continue

            # We have at least one m3u8 URL - wait a bit more for the player
            # to potentially try fallback servers (which might have valid content)
            if m3u8_responses and not has_good_m3u8():
                log("Have m3u8 URL(s) but no body yet, waiting for fallback servers...")
                start = time.time()
                while not has_good_m3u8() and (time.time() - start) < 15:
                    page.wait_for_timeout(2000)
                    log(f"Waiting for fallback... ({len(m3u8_responses)} responses so far, {int(time.time() - start)}s)")

            # If no m3u8 at all, wait longer
            if not m3u8_responses:
                log("No m3u8 found, waiting longer...")
                start = time.time()
                while not m3u8_responses and (time.time() - start) < 30:
                    page.wait_for_timeout(2000)
                    log(f"Still waiting... ({int(time.time() - start)}s elapsed)")

        except Exception as e:
            log(f"Navigation error: {e}")
        finally:
            browser.close()

    # Pick the best m3u8 response
    log(f"Total m3u8 responses collected: {len(m3u8_responses)}")
    for i, r in enumerate(m3u8_responses):
        log(f"  [{i}] status={r['status']} has_content={r['content'] is not None} url={r['url'][:80]}...")

    if not m3u8_responses:
        return {'success': False, 'error': 'No m3u8 URL intercepted within timeout'}

    # Prefer responses with actual m3u8 content, then 200 status, then any
    best = None
    for r in m3u8_responses:
        if r['content']:
            best = r
            break
    if not best:
        for r in m3u8_responses:
            if r['status'] >= 200 and r['status'] < 300:
                best = r
                break
    if not best:
        best = m3u8_responses[0]

    referer = best['referer'] or 'https://bingeflix.tv/'

    result = {
        'success': True,
        'hls_url': best['url'],
        'subtitles': subtitles,
        'referer': referer,
    }
    if best['content']:
        result['hls_content'] = best['content']
    return result


def main():
    if len(sys.argv) < 3:
        print(json.dumps({
            'success': False,
            'error': 'Usage: python3 bingeflix_scraper.py <tmdb_id> <type> [season] [episode]'
        }))
        sys.exit(1)

    tmdb_id = sys.argv[1]
    content_type = sys.argv[2]
    season = sys.argv[3] if len(sys.argv) > 3 else None
    episode = sys.argv[4] if len(sys.argv) > 4 else None

    log(f"Starting scrape: tmdb_id={tmdb_id}, type={content_type}, season={season}, episode={episode}")

    try:
        result = scrape(tmdb_id, content_type, season, episode)
    except Exception as e:
        log(f"Fatal error: {e}")
        result = {'success': False, 'error': str(e)}

    # Output JSON to stdout (only output)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
