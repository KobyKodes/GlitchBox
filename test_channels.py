#!/usr/bin/env python3
"""
Script to test live TV channel URLs and identify broken ones
"""

import requests
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

# Read channels from HTML file
def extract_channels_from_html(html_file):
    """Extract channel URLs from the HTML file"""
    channels = []

    with open(html_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the allWorldChannels array
    pattern = r'\{ name: \'([^\']+)\', url: \'([^\']+)\', category: \'([^\']+)\', country: \'([^\']+)\'[^}]*\}'
    matches = re.findall(pattern, content)

    for match in matches:
        name, url, category, country = match
        channels.append({
            'name': name,
            'url': url,
            'category': category,
            'country': country
        })

    return channels

def test_channel(channel, timeout=10):
    """Test if a channel URL is accessible"""
    url = channel['url']
    name = channel['name']

    try:
        # Try HEAD request first (faster)
        response = requests.head(url, timeout=timeout, allow_redirects=True)

        # If HEAD fails, try GET
        if response.status_code >= 400:
            response = requests.get(url, timeout=timeout, stream=True, allow_redirects=True)

        if response.status_code == 200:
            return {
                'status': 'OK',
                'name': name,
                'url': url,
                'country': channel['country'],
                'category': channel['category']
            }
        else:
            return {
                'status': 'FAILED',
                'name': name,
                'url': url,
                'country': channel['country'],
                'category': channel['category'],
                'error': f'HTTP {response.status_code}'
            }

    except requests.exceptions.Timeout:
        return {
            'status': 'FAILED',
            'name': name,
            'url': url,
            'country': channel['country'],
            'category': channel['category'],
            'error': 'Timeout'
        }
    except requests.exceptions.ConnectionError:
        return {
            'status': 'FAILED',
            'name': name,
            'url': url,
            'country': channel['country'],
            'category': channel['category'],
            'error': 'Connection Error'
        }
    except Exception as e:
        return {
            'status': 'FAILED',
            'name': name,
            'url': url,
            'country': channel['country'],
            'category': channel['category'],
            'error': str(e)
        }

def main():
    html_file = '/Users/jadkoby/Desktop/GlitchBox/movie_tv_player.html'

    print("Extracting channels from HTML...")
    channels = extract_channels_from_html(html_file)
    print(f"Found {len(channels)} channels")

    print("\nTesting channels (this may take a while)...")
    print("=" * 80)

    working = []
    broken = []

    # Test channels concurrently for speed
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_channel = {executor.submit(test_channel, channel): channel for channel in channels}

        for i, future in enumerate(as_completed(future_to_channel), 1):
            result = future.result()

            if result['status'] == 'OK':
                working.append(result)
                print(f"[{i}/{len(channels)}] ✅ {result['name']} ({result['country']})")
            else:
                broken.append(result)
                print(f"[{i}/{len(channels)}] ❌ {result['name']} ({result['country']}) - {result['error']}")

    print("\n" + "=" * 80)
    print(f"\n📊 SUMMARY:")
    print(f"Total channels: {len(channels)}")
    print(f"Working: {len(working)} ({len(working)/len(channels)*100:.1f}%)")
    print(f"Broken: {len(broken)} ({len(broken)/len(channels)*100:.1f}%)")

    # Group broken channels by country
    print(f"\n❌ BROKEN CHANNELS BY COUNTRY:")
    broken_by_country = {}
    for channel in broken:
        country = channel['country']
        if country not in broken_by_country:
            broken_by_country[country] = []
        broken_by_country[country].append(channel)

    for country, channels_list in sorted(broken_by_country.items()):
        print(f"\n{country} ({len(channels_list)} broken):")
        for ch in channels_list:
            print(f"  - {ch['name']}: {ch['error']}")

    # Save results to JSON
    with open('/Users/jadkoby/Desktop/GlitchBox/channel_test_results.json', 'w') as f:
        json.dump({
            'working': working,
            'broken': broken,
            'summary': {
                'total': len(channels),
                'working': len(working),
                'broken': len(broken)
            }
        }, f, indent=2)

    print(f"\n💾 Full results saved to: channel_test_results.json")

if __name__ == '__main__':
    main()
