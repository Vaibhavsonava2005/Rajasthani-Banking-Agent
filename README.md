# Rajasthani Banking Voice AI Agent

An enterprise-grade, highly-optimized voice AI platform built to deliver dynamic, personalized loan-reminder calls in Rajasthani and Hindi.

## Features

- **Exotel Telephony Integration:** Hardcoded and deeply optimized for Exotel's REST API, ensuring ultra-low latency and maximum reliability for outbound calling in India.
- **Dynamic Text-To-Speech (TTS):** Bypasses expensive third-party AI voice processors by generating and serving dynamic `text/plain` responses directly to Exotel's native TTS engines, minimizing per-minute call costs.
- **Rajasthani Number Translation:** Automatically converts numeric loan values, EMIs, and balances into pure Hindi/Rajasthani text strings, ensuring the AI voice sounds completely natural to rural users.
- **Serverless Architecture:** Fully deployed on Vercel with zero cold-boot overhead by stripping unnecessary dependencies.
- **Excel/CSV Ingestion:** Instantly processes banking data formats and normalizes lender names for accurate phonetic pronunciation.
- **Real-Time Call Tracking:** Tracks active calls, busy signals, and completions dynamically across the dashboard.

## Tech Stack
- **Backend:** Python, Flask, Gunicorn
- **Deployment:** Vercel (Serverless Functions)
- **Telephony:** Exotel
- **Frontend:** Vanilla JS, HTML/CSS (Dynamic Polling)

## Setup & Deployment

1. **Environment Variables:**
   Ensure the following environment variables are securely added to your Vercel deployment:
   - `EXOTEL_ACCOUNT_SID`: Your Exotel Account SID.
   - `EXOTEL_API_KEY`: Your Exotel API Key.
   - `EXOTEL_API_TOKEN`: Your Exotel API Token.
   - `EXOTEL_PHONE_NUMBER`: Your Exophone virtual number (e.g., `08047112345`).
   - `EXOTEL_SUBDOMAIN`: Exotel cluster URL (e.g., `api.exotel.com`).
   - `PUBLIC_BASE_URL`: The production URL of your Vercel app.

2. **Run Locally:**
   ```bash
   pip install -r requirements.txt
   flask run
   ```

3. **Deploy:**
   Trigger a deployment via the Vercel CLI:
   ```bash
   npx vercel --prod
   ```

*Built for maximum efficiency and scale in the Indian micro-finance sector.*
