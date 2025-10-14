# RetroFlix.com - Public Production Version

A retro-themed movie and TV streaming site with monetization via PropellerAds and Google AdSense.

## 🎯 Purpose

This is the **PUBLIC VERSION** of Jad's Awesome Streaming Site, configured for:
- Production deployment
- Ad monetization (PropellerAds + Google AdSense)
- Secure environment variables
- Free hosting on Render.com

## 📁 Project Structure

```
RetroFlix-Public/
├── movie_api.py                    # Main Flask server (production-ready)
├── movie_tv_player.html            # Frontend HTML
├── requirements.txt                # Python dependencies
├── Procfile                        # Heroku/Render deployment config
├── render.yaml                     # Render.com configuration
├── runtime.txt                     # Python version specification
├── .env.example                    # Environment variables template
├── .gitignore                      # Git ignore rules
├── AD_INTEGRATION_SNIPPETS.html   # Ad integration code snippets
├── DEPLOYMENT_GUIDE.md            # Complete step-by-step deployment guide
└── README.md                       # This file
```

## 🚀 Quick Start

### 1. Read the Deployment Guide First!
```bash
open DEPLOYMENT_GUIDE.md
```

### 2. Set Up Environment Variables
```bash
cp .env.example .env
# Edit .env and add your actual API keys
```

### 3. Test Locally
```bash
pip install -r requirements.txt
python movie_api.py
# Visit http://localhost:5001
```

### 4. Deploy to Render.com (FREE)
Follow complete instructions in `DEPLOYMENT_GUIDE.md`

## 💰 Monetization Setup

### Step 1: PropellerAds (Instant Revenue)
1. Sign up: https://propellerads.com/
2. Add your site
3. Get Zone IDs
4. Integrate ads using `AD_INTEGRATION_SNIPPETS.html`

### Step 2: Google AdSense (Higher Quality)
1. Deploy site first
2. Apply: https://www.google.com/adsense
3. Wait for approval (1-2 weeks)
4. Add ad codes

See `DEPLOYMENT_GUIDE.md` for detailed instructions.

## 🔐 Security Features

✅ API keys loaded from environment variables
✅ Secret key auto-generated
✅ CORS configured
✅ .gitignore protects sensitive files
✅ Production-ready server configuration

## 📊 Expected Revenue

With 100 concurrent users (~1000 daily visitors):
- **PropellerAds Only**: $2-10/day
- **PropellerAds + AdSense**: $3-15/day
- **Monthly**: $90-450/month

Scale up traffic = scale up revenue!

## 🆓 Hosting (Free Options)

- **Render.com** ⭐ RECOMMENDED
  - Free tier: 750 hours/month
  - Auto SSL, custom domain
  - Easy deployment

- **Railway.app**
  - $5 free credit/month
  - No sleep

- **Fly.io**
  - Good performance
  - Free tier available

## 🛠️ Tech Stack

- **Backend**: Flask + SocketIO
- **APIs**: TMDB, OMDB
- **Streaming**: VidKing, SuperEmbed
- **Server**: Gunicorn + Eventlet
- **Hosting**: Render.com (free)
- **Ads**: PropellerAds + Google AdSense

## 📝 To-Do Before Launch

- [ ] Buy domain (retroflix.com) - ~$10/year
- [ ] Push code to GitHub
- [ ] Deploy to Render.com (FREE)
- [ ] Connect custom domain
- [ ] Sign up for PropellerAds
- [ ] Integrate ad codes
- [ ] Create Privacy Policy page
- [ ] Create Terms of Service page
- [ ] Apply to Google AdSense (optional)

## 🚨 Important Notes

### This is the PUBLIC version
- Has ads for monetization
- Safe to deploy publicly
- Secure (no hardcoded secrets)

### Keep your PRIVATE version separate!
- Located in "Jads Awesome Site.app"
- Use with friends via ngrok
- No ads, no tracking
- NEVER push to public GitHub

## 📚 Documentation

- `DEPLOYMENT_GUIDE.md` - Complete deployment walkthrough
- `AD_INTEGRATION_SNIPPETS.html` - Ad code examples
- `.env.example` - Environment variables template

## 🆘 Support

Need help? Check:
1. `DEPLOYMENT_GUIDE.md` (covers everything)
2. Render.com docs: https://render.com/docs
3. PropellerAds support: support@propellerads.com

## 📈 Scaling Up

Once your site is earning money:
1. Optimize ad placements
2. Add SEO (meta tags, descriptions)
3. Market on social media
4. Consider paid hosting for better performance
5. Add more features to increase engagement

## ⚖️ Legal

- Add Privacy Policy (required for AdSense)
- Add Terms of Service
- Comply with GDPR/CCPA if needed
- Disclaim third-party hosted content

See `DEPLOYMENT_GUIDE.md` for templates and resources.

---

**Ready to launch?** Start with `DEPLOYMENT_GUIDE.md` and follow step-by-step! 🚀
