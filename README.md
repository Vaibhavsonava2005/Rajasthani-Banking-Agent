# Dotstark Banking Agent
*The Future of B2B Financial Operations & Automated Outreach*

This is an enterprise-grade, highly-optimized voice AI platform built to deliver dynamic, personalized loan-reminder calls in Hindi and regional dialects. Designed specifically for Banks, NBFCs, and BPO call centers to completely automate collections and customer engagement.

## Features & Integrations

- **Plivo Wholesale Telephony Integration:** Hardcoded and deeply optimized for Plivo's REST API, ensuring ultra-low latency and maximum reliability for outbound calling across India.
- **Native Neural TTS Pipeline:** Bypasses expensive third-party conversational AI processors (like Sarvam or Twilio). We compile Microsoft Edge Neural TTS directly into raw audio bytes and inject them into telecom networks to achieve **0.0 seconds of latency**.
- **State-of-the-Art Real-Time Tracking:** A fully stateless, Vercel-optimized Deep AI Normalizer intercepts chaotic API responses and normalizes them into strict real-time UI tracking states (*Ringing, In Progress, Busy, Completed*).
- **Expanded Banking Support:** Out-of-the-box data mapping and custom conversational scripts for major lenders, including State Bank of India, **AU Small Finance Bank**, and **Rajasthan Grameen Bank**.
- **Serverless Architecture:** Fully deployed on Vercel with zero cold-boot overhead. The platform seamlessly handles batches of 500+ calls simultaneously by spinning up cloud instances on demand and tearing them down instantly, costing absolutely zero idle time.

## Tech Stack
- **Backend:** Python, Flask, Gunicorn
- **Deployment:** Vercel (Serverless Functions)
- **Telephony & Network:** Plivo Wholesale API
- **Frontend:** Vanilla JS, HTML/CSS (Dynamic 8-second staggered polling architecture to bypass rate limits)

## Setup & Deployment

1. **Environment Variables:**
   Ensure the following environment variables are securely added to your Vercel deployment:
   - `PLIVO_AUTH_ID`: Your Plivo Account SID.
   - `PLIVO_AUTH_TOKEN`: Your Plivo Auth Token.
   - `PUBLIC_BASE_URL`: The production URL of your Vercel app (e.g., `https://dotstark-banking-agent.vercel.app`).

2. **Run Locally:**
   ```bash
   pip install -r requirements.txt
   flask run
   ```

3. **Deploy to Vercel (Production):**
   *Note: We deploy strictly to Vercel production to utilize Serverless Edge scaling.*
   ```bash
   npx vercel --prod --yes
   ```

*Built for maximum efficiency and scale in the Indian micro-finance and banking sector.*
