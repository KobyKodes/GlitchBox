#!/bin/bash
# Start all GlitchBox services locally

echo "Starting GlitchBox locally..."
echo ""

# Kill any existing processes on our ports
lsof -ti:4000 | xargs kill 2>/dev/null
lsof -ti:5001 | xargs kill 2>/dev/null
lsof -ti:8000 | xargs kill 2>/dev/null

sleep 1

# Start VidNest scraper (port 4000)
echo "[1/3] Starting VidNest scraper on port 4000..."
cd /Users/jadkoby/Developer/GlitchBox/vidsrc-ts
node server.js &
SCRAPER_PID=$!

sleep 2

# Start Flask backend (port 5001)
echo "[2/3] Starting Flask backend on port 5001..."
cd /Users/jadkoby/Developer/GlitchBox
python3 movie_api.py &
FLASK_PID=$!

sleep 2

# Start frontend server (port 8000)
echo "[3/3] Starting frontend on port 8000..."
cd /Users/jadkoby/Developer/GlitchBox
python3 -m http.server 8000 &
FRONTEND_PID=$!

echo ""
echo "============================================"
echo "GlitchBox is running!"
echo "============================================"
echo ""
echo "  Frontend:  http://localhost:8000"
echo "  Backend:   http://localhost:5001"
echo "  Scraper:   http://localhost:4000"
echo ""
echo "Press Ctrl+C to stop all services"
echo ""

# Wait for Ctrl+C
trap "echo 'Stopping...'; kill $SCRAPER_PID $FLASK_PID $FRONTEND_PID 2>/dev/null; exit" SIGINT SIGTERM

# Keep script running
wait
