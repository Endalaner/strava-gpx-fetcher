# Strava Telegram Bot with Community Hub (Russian/Kabanya Edition)

A Telegram userbot/assistant that unfurls Strava links with rich details, manages group rides, and integrates weather forecasts with a unique "Kabanya" (Wild Boar) persona.

## Features

-   **Link Unfurling**: Automatically converts Strava links (Activities, Routes, Group Events) into detailed cards with maps (.gpx), distance, and elevation.
-   **Community Hub**:
    -   **Announce Rides**: Turn any Strava Group Event link into an interactive "Ride Card".
    -   **RSVP System**: "I'm In" (🐗) and "Maybe" (🐌) buttons for users to join the pack.
    -   **Live Updates**: Ride cards update in real-time as users join.
    -   **Reminders**: Automated ping 1 hour before the ride start.
-   **Weather Integration**:
    -   Fetches forecast for the exact ride start time and location.
    -   Displays Temp, Wind (with directional arrows), and Rain probability.
    -   **"Kabanya" Persona**: Fun, boar-themed commentary based on weather conditions (e.g., "Tailwind alert! We fly!").
-   **Russian Localization**: Fully localized UI and messages.

## Setup

### Prerequisites

-   Docker & Docker Compose
-   Strava API Application (https://www.strava.com/settings/api)
-   Telegram Bot Token (@BotFather)
-   OpenWeatherMap API Key (Free Tier)

### Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/yourusername/strava-bot.git
    cd strava-bot
    ```

2.  Configure Environment:
    ```bash
    cp .env.example .env
    # Edit .env and fill in your credentials
    ```

3.  Run with Docker Compose:
    ```bash
    docker-compose up -d --build
    ```

## Commands

### General
-   `/ping`: Connectivity check. Bot responds with 🐗.
-   `/db_check`: Verifies the bot can reach the database at runtime.

### Weather
-   `/forecast`: Manually trigger the weekend weather broadcast for the group. Posts a detailed Saturday/Sunday forecast with cycling-specific ratings.
-   `/tomorrow`: Fetches tomorrow's forecast for the default location and posts it to the group. The header adapts to conditions — great, rainy, cold, windy, or bad.

### Bot Relay (Megaphone)
-   **DM only**, for authorized users:
-   `/relay <text>`: Forwards the message text to the group chat as the bot.
-   **Media Relay**: Send a photo or document to the bot; it will be forwarded to the group with the caption.
-   **Passthrough Mode**: If `BOT_RELAY_MODE=passthrough` is set in `.env`, *any* text sent to the bot (without a command) will be relayed.

## Usage

1.  Add the bot to your Telegram group (give it "Pin Messages" permission).
2.  Send a Strava Group Event link (e.g., `https://www.strava.com/clubs/123/group_events/456`).
3.  The bot will reply with a file and an **"📢 Анонс"** button.
4.  Click **"Анонс"** to create a pinned Ride Card.
5.  Users can click **"🐗 Я В ДЕЛЕ!"** to join.

## License

MIT
