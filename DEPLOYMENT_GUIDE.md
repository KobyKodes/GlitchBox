# RetroFlix.com - Complete Deployment Guide

## 🚀 Quick Start Summary

**Goal**: Deploy RetroFlix.com with PropellerAds + Google AdSense for $0/month
**Expected Traffic**: 100 concurrent users
**Expected Revenue**: $2-15/day (1000 daily visitors)

---

## 📋 Pre-Deployment Checklist

### 1. Domain Name
- [ ] Purchase retroflix.com from:
  - **Namecheap** ($8-12/year) - RECOMMENDED
  - **Google Domains** ($12/year)
  - **GoDaddy** ($10-15/year)

### 2. Ad Network Signups
- [ ] **PropellerAds** (https://propellerads.com/) - Sign up FIRST (instant approval)
- [ ] **Google AdSense** (https://www.google.com/adsense) - Apply after site is live

---

## 🆓 Free Hosting Options (Recommended)

### Option 1: Render.com (BEST FOR YOU)
**Pros**: Easy setup, free SSL, custom domain support, 750hrs/month free
**Cons**: Site spins down after 15 min inactivity (15-30 sec cold start)

### Option 2: Railway.app
**Pros**: $5 free credit/month, faster, no sleep
**Cons**: Limited free tier

### Option 3: Fly.io
**Pros**: Good performance, free tier
**Cons**: More complex setup

---

## 📦 Step-by-Step: Deploy to Render.com (FREE)

### Step 1: Prepare Your Code

1. **Initialize Git Repository** (if not already):
   ```bash
   cd /Users/jadkoby/Desktop/RetroFlix-Public
   git init
   git add .
   git commit -m "Initial commit - RetroFlix public version"
   ```

2. **Push to GitHub**:
   - Create a new private repository on GitHub: https://github.com/new
   - Name it: `retroflix-public`
   - Run these commands:
   ```bash
   git remote add origin https://github.com/YOUR_USERNAME/retroflix-public.git
   git branch -M main
   git push -u origin main
   ```

### Step 2: Deploy to Render.com

1. **Sign up**: https://render.com/ (use your GitHub account)

2. **Create New Web Service**:
   - Click "New +" → "Web Service"
   - Connect your GitHub repository: `retroflix-public`
   - Configure:
     - **Name**: `retroflix`
     - **Environment**: Python 3
     - **Build Command**: `pip install -r requirements.txt`
     - **Start Command**: `gunicorn --worker-class eventlet -w 1 movie_api:app`
     - **Plan**: Free

3. **Add Environment Variables**:
   - Click "Environment" tab
   - Add these:
     ```
     TMDB_API_KEY = e577f3394a629d69efa3a9414e172237
     OMDB_API_KEY = ecbf499d
     SECRET_KEY = [Click "Generate" button]
     PYTHON_VERSION = 3.11.0
     ```

4. **Deploy**:
   - Click "Create Web Service"
   - Wait 5-10 minutes for deployment
   - You'll get a URL like: `https://retroflix.onrender.com`

### Step 3: Connect Custom Domain

1. **In Render Dashboard**:
   - Go to your service → "Settings" → "Custom Domain"
   - Click "Add Custom Domain"
   - Enter: `retroflix.com` and `www.retroflix.com`

2. **In Namecheap (or your registrar)**:
   - Go to Domain List → Manage → Advanced DNS
   - Add these records:
     ```
     Type: CNAME Record
     Host: www
     Value: retroflix.onrender.com
     TTL: Automatic

     Type: A Record
     Host: @
     Value: [Get IP from Render.com docs]
     TTL: Automatic
     ```
   - Wait 10-60 minutes for DNS propagation

3. **SSL Certificate** (Auto-enabled by Render)

---

## 💰 Step-by-Step: Add Monetization

### Phase 1: PropellerAds (Start Immediately)

1. **Sign Up**:
   - Go to: https://propellerads.com/
   - Sign up as "Publisher"
   - Verify email

2. **Add Website**:
   - Dashboard → "Websites" → "Add Website"
   - Enter: `https://retroflix.com`
   - Category: Entertainment / Video Streaming
   - **Instant Approval ✓**

3. **Create Ad Zones**:
   - Go to "Ad Zones" → "Create Ad Zone"
   - **Banner Ads** (Best for streaming sites):
     - Type: Banner
     - Size: 970x90 (Top banner), 728x90 (Content)
     - Get Zone ID

   - **Popunder Ads** (High revenue):
     - Type: Onclick (Popunder)
     - Get Zone ID

   - **Push Notifications** (Passive income):
     - Type: Push Notifications
     - Get Zone ID

4. **Integrate Ad Codes**:
   - Open `AD_INTEGRATION_SNIPPETS.html` in this folder
   - Replace `YOUR_PROPELLER_ZONE_ID` with actual Zone IDs
   - Add codes to `movie_tv_player.html` (follow instructions in file)

5. **Revenue Starts Immediately** 🎉

### Phase 2: Google AdSense (After Site is Live)

1. **Wait 1-2 Weeks** for site to have content and traffic

2. **Apply to AdSense**:
   - Go to: https://www.google.com/adsense
   - Click "Sign Up"
   - Add site: `retroflix.com`
   - Add required code to `<head>` section

3. **Approval Process** (1-2 weeks):
   - Google reviews your site
   - Requirements:
     - Original content ✓ (your site)
     - Good user experience ✓
     - Complies with policies (check streaming content usage)

4. **If Approved**:
   - Create Ad Units in AdSense dashboard
   - Replace placeholders in `AD_INTEGRATION_SNIPPETS.html`
   - Deploy updated HTML

5. **If Rejected**:
   - Keep using PropellerAds
   - Try alternatives: Media.net, Ezoic

---

## 💵 Revenue Expectations

### With PropellerAds Only:
- **100 concurrent users** = ~1,000 daily visitors
- **CPM**: $2-10 per 1000 visitors
- **Daily Revenue**: $2-10/day
- **Monthly Revenue**: $60-300/month

### With PropellerAds + Google AdSense:
- **Combined CPM**: $3-15
- **Daily Revenue**: $3-15/day
- **Monthly Revenue**: $90-450/month

### How to Scale Up:
1. **SEO Optimization**:
   - Add movie reviews/descriptions
   - Optimize page titles and meta tags
   - Submit sitemap to Google

2. **Social Media**:
   - Share on Reddit (r/MovieSuggestions, etc.)
   - TikTok/Instagram clips
   - YouTube trailer compilations

3. **Paid Ads** (when profitable):
   - Google Ads
   - Facebook Ads
   - Target movie keywords

---

## 🔧 Maintenance & Optimization

### Monitor Performance:
1. **Render.com Dashboard**: Check uptime and errors
2. **PropellerAds Dashboard**: Track revenue and ad performance
3. **Google Analytics** (optional): Add tracking code

### Update Code:
```bash
git add .
git commit -m "Updated ads/features"
git push
# Render auto-deploys on push!
```

### Handle Cold Starts (Render Free Tier):
- Site sleeps after 15 min inactivity
- First visitor waits 15-30 seconds
- **Solution**: Use a "ping" service to keep it awake:
  - https://uptimerobot.com/ (free)
  - Ping your site every 5 minutes

---

## 🚨 Important Legal & Compliance

### 1. Create Privacy Policy Page:
- Required for AdSense approval
- Disclose use of cookies and ads
- Use generator: https://www.privacypolicygenerator.info/

### 2. Create Terms of Service Page:
- Protect yourself legally
- Use generator: https://www.termsofservicegenerator.net/

### 3. Copyright Compliance:
- **VidKing/SuperEmbed**: You're embedding third-party streams
- **Disclaimer**: Add "All content hosted by third parties"
- **DMCA**: Set up DMCA takedown process (just an email contact)

### 4. GDPR/CCPA (if needed):
- Add cookie consent banner (required in EU)
- Use: https://www.cookiebot.com/ (free tier)

---

## 🐛 Troubleshooting

### Site Won't Load:
- Check Render logs for errors
- Verify environment variables are set
- Check DNS settings propagated (use dnschecker.org)

### Ads Not Showing:
- Verify ad codes are properly integrated
- Check browser ad blocker is disabled
- PropellerAds: Check zone IDs are correct
- AdSense: Wait for approval first

### Database/Cache Issues:
- Current setup uses in-memory storage
- Resets on deployment
- **Upgrade**: Add Redis for production (Render free tier available)

### Revenue Lower Than Expected:
- Traffic source matters (US/UK = higher CPM)
- Ad placement affects revenue
- Try different ad sizes and positions
- Enable PropellerAds popunders (higher revenue)

---

## 📈 Next Steps After Launch

1. **Week 1-2**:
   - ✓ Deploy to Render.com
   - ✓ Connect domain
   - ✓ Integrate PropellerAds
   - ✓ Start earning!

2. **Week 3-4**:
   - Apply to Google AdSense
   - Add Privacy Policy & ToS pages
   - Set up Google Analytics
   - Start SEO optimization

3. **Month 2+**:
   - Analyze which ads perform best
   - Optimize ad placements
   - Scale traffic with marketing
   - Consider upgrading to paid hosting if needed

---

## 💪 Keep Your Private Version Safe!

**Your private version** (in "Jads Awesome Site.app"):
- ✓ Keep for personal use with friends
- ✓ Use with ngrok for watch parties
- ✓ No ads, no tracking
- ✓ NEVER push to public GitHub

**Public version** (in RetroFlix-Public):
- ✓ Has ads for monetization
- ✓ Safe to make public
- ✓ API keys in environment variables (secure)

---

## 🆘 Need Help?

- **Render.com Docs**: https://render.com/docs
- **PropellerAds Support**: support@propellerads.com
- **Google AdSense Help**: https://support.google.com/adsense

---

## 🎉 Ready to Launch?

Follow the steps above and you'll have RetroFlix.com live and earning money within a few hours!

**Summary**:
1. Buy domain → $10/year
2. Deploy to Render.com → FREE
3. Add PropellerAds → Instant revenue
4. Apply to AdSense → More revenue later
5. Scale traffic → More money!

Good luck! 🚀💰
