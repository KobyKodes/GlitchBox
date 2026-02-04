/**
 * Custom VidSrc Scraper
 * Extracts m3u8 streams from vidsrc.net using HTTP requests only (no Playwright)
 */

async function fetchWithHeaders(url, referer = null) {
  const headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
  };
  if (referer) headers['Referer'] = referer;

  const resp = await fetch(url, { headers });
  return resp.text();
}

function extractStreamServers(html) {
  // Extract only streaming servers (tmstr, fasdf, app2, etc.) - not CDN libraries
  const serverRegex = /https?:\/\/((?:tmstr\d*|fasdf\d*|app\d*)\.(?:neonhorizonworkshops|wanderlynest|orchidpixelgardens|cloudnestra)\.com)/gi;
  const matches = html.match(serverRegex) || [];
  const servers = [...new Set(matches.map(m => {
    try { return new URL(m).hostname; } catch { return null; }
  }).filter(Boolean))];
  return servers;
}

function extractM3u8Urls(html) {
  // Extract m3u8 URLs, looking for the 'file:' pattern which contains all variants
  const fileRegex = /file:\s*["']([^"']+)["']/gi;
  const results = [];
  let match;

  while ((match = fileRegex.exec(html)) !== null) {
    const fileContent = match[1];
    // Split by ' or ' to get individual URLs
    const urls = fileContent.split(/\s+or\s+/).map(u => u.trim()).filter(u => u.includes('.m3u8'));
    results.push(...urls);
  }

  return results;
}

function resolveM3u8Url(urlTemplate, servers) {
  let url = urlTemplate;

  // Check for placeholder patterns like tmstr5.{v1} or {v1}
  if (url.includes('{v')) {
    // Find server domain to use
    const serverDomain = servers.find(s => s.includes('neonhorizonworkshops')) ||
                         servers.find(s => s.includes('wanderlynest')) ||
                         servers.find(s => s.includes('cloudnestra')) ||
                         servers[0] ||
                         'neonhorizonworkshops.com';

    // Extract the base domain (e.g., neonhorizonworkshops.com from tmstr1.neonhorizonworkshops.com)
    const baseDomain = serverDomain.replace(/^[a-z]+\d*\./, '');

    // Replace patterns like tmstr5.{v1} with tmstr5.neonhorizonworkshops.com
    url = url.replace(/([a-z]+\d*)\.?\{v\d+\}/gi, (match, prefix) => {
      if (prefix) {
        return `${prefix}.${baseDomain}`;
      }
      return serverDomain;
    });
  }

  return url;
}

async function extractSubtitles(html) {
  // Look for subtitle URLs - VTT or SRT files
  const subtitleRegex = /https?:\/\/[^\s"'<>]+\.(?:vtt|srt)[^\s"'<>]*/gi;
  const matches = html.match(subtitleRegex) || [];
  return [...new Set(matches)];
}

export async function scrapeVidsrc(tmdbId, type = 'movie', season = null, episode = null) {
  try {
    // Step 1: Fetch the embed page
    const embedUrl = type === 'movie'
      ? `https://vidsrc.net/embed/movie?tmdb=${tmdbId}`
      : `https://vidsrc.net/embed/tv?tmdb=${tmdbId}&season=${season}&episode=${episode}`;

    console.log(`[Scraper] Fetching embed: ${embedUrl}`);
    const embedHtml = await fetchWithHeaders(embedUrl);

    // Step 2: Extract iframe src (RCP URL)
    const iframeMatch = embedHtml.match(/iframe[^>]+src="([^"]+)"/);
    if (!iframeMatch) {
      console.log('[Scraper] No iframe found in embed page');
      return null;
    }

    let rcpUrl = iframeMatch[1];
    if (rcpUrl.startsWith('//')) rcpUrl = 'https:' + rcpUrl;
    console.log(`[Scraper] Fetching RCP: ${rcpUrl.substring(0, 80)}...`);

    // Step 3: Fetch RCP page
    const rcpHtml = await fetchWithHeaders(rcpUrl, 'https://vidsrc.net/');

    // Step 4: Extract prorcp path
    const srcMatch = rcpHtml.match(/src:\s*'([^']+)'/);
    if (!srcMatch) {
      console.log('[Scraper] No prorcp path found');
      return null;
    }

    const prorcpPath = srcMatch[1];
    const baseUrl = new URL(rcpUrl).origin;
    const prorcpUrl = baseUrl + prorcpPath;
    console.log(`[Scraper] Fetching PRORCP: ${prorcpUrl.substring(0, 80)}...`);

    // Step 5: Fetch prorcp page (contains the m3u8 URLs!)
    const prorcpHtml = await fetchWithHeaders(prorcpUrl, rcpUrl);

    // Step 6: Extract servers and m3u8 URLs
    const servers = extractStreamServers(prorcpHtml);
    console.log(`[Scraper] Found ${servers.length} streaming servers`);

    const m3u8Urls = extractM3u8Urls(prorcpHtml);
    console.log(`[Scraper] Found ${m3u8Urls.length} m3u8 URLs`);

    if (m3u8Urls.length === 0) {
      console.log('[Scraper] No m3u8 URLs found');
      return null;
    }

    // Step 7: Resolve the first m3u8 URL (replace placeholders)
    const resolvedUrl = resolveM3u8Url(m3u8Urls[0], servers);
    console.log(`[Scraper] Resolved m3u8: ${resolvedUrl}`);

    // Step 8: Extract subtitles
    const subtitles = await extractSubtitles(prorcpHtml);
    console.log(`[Scraper] Found ${subtitles.length} subtitles`);

    return {
      success: true,
      stream: resolvedUrl,
      subtitles: subtitles,
      referer: baseUrl
    };

  } catch (error) {
    console.error('[Scraper] Error:', error.message);
    return null;
  }
}

// Test if running directly
if (import.meta.url === `file://${process.argv[1]}`) {
  console.log('=== Testing custom scraper ===\n');

  // Test TV show
  console.log('Test 1: Breaking Bad S01E01');
  let result = await scrapeVidsrc('1396', 'tv', 1, 1);
  console.log('Result:', result ? `SUCCESS - ${result.stream.substring(0, 80)}...` : 'FAILED');

  console.log('\n--- Running again to test consistency ---\n');

  // Test again
  console.log('Test 2: Breaking Bad S01E01 (repeat)');
  result = await scrapeVidsrc('1396', 'tv', 1, 1);
  console.log('Result:', result ? `SUCCESS - ${result.stream.substring(0, 80)}...` : 'FAILED');

  console.log('\n--- Testing movie ---\n');

  // Test movie
  console.log('Test 3: Fight Club (TMDB 550)');
  result = await scrapeVidsrc('550', 'movie');
  console.log('Result:', result ? `SUCCESS - ${result.stream.substring(0, 80)}...` : 'FAILED');
}
