# Renpho Health to Garmin Connect Sync 🔄

![Docker Image Size (latest)](https://img.shields.io/docker/image-size/zduts/renpho-garmin-sync/latest)
![Docker Pulls](https://img.shields.io/docker/pulls/zduts/renpho-garmin-sync)
![Architecture](https://img.shields.io/badge/arch-amd64%20%7C%20arm64-blue)

A lightweight, Dockerized service to synchronize your body weight and composition data from the **Renpho Health** app directly to **Garmin Connect**.

> **Note**: This project was "vibecoded" (AI-assisted), but is actively supported and maintained. 🤖✨

## ✨ Features

- **Direct Sync**: Connects directly to the *new* Renpho Health API (AES Encrypted). No intermediate bridges (Fitbit/Google Fit) required.
- **Secure**: Uses your native app credentials securely.
- **Automated**: Runs immediately on startup, then schedules a daily sync at your preferred time.
- **Multi-Arch**: Native support for **AMD64** (Standard Server/PC) and **ARM64** (Raspberry Pi/Apple Silicon).
- **Rich Data**: Syncs Weight, Body Fat %, Water %, Bone Mass, Muscle Mass, and Visceral Fat.

## ⚠️ Limitations

- **No Historical Backlog**: Due to limitations in the current Renpho Health API endpoint (`dailyCalories`), we can only reliable fetch the *latest* measurement. Therefore, this tool **cannot** backfill your historical data. It is designed to keep your *future* data in sync day-by-day.
- **Daily Only**: It syncs the latest measurement available for "Today".

## 🚀 Deployment

The easiest way to run this is via Docker Compose (Portainer friendly).

### docker-compose.yml

```yaml
services:
  renpho-garmin-sync:
    image: zduts/renpho-garmin-sync:latest
    container_name: renpho_garmin_sync
    restart: unless-stopped
    environment:
      - RENPHO_EMAIL=your_renpho_email@example.com
      - RENPHO_PASSWORD=your_renpho_password
      - GARMIN_EMAIL=your_garmin_email@example.com
      - GARMIN_PASSWORD=your_garmin_password
      - SYNC_TIME=03:00  # Time to run daily tasks (HH:MM format, 24h)
      - TZ=Europe/London # Your Timezone
    volumes:
      - ./data:/app/data # Optional: For logging/persistence if needed later
```

### Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `RENPHO_EMAIL` | Email used for Renpho Health App | **Required** |
| `RENPHO_PASSWORD` | Password for Renpho Health App | **Required** |
| `GARMIN_EMAIL` | Email used for Garmin Connect | **Required** |
| `GARMIN_PASSWORD` | Password for Garmin Connect | **Required** |
| `SYNC_TIME` | Time to run the daily sync (HH:MM) | `03:00` |
| `TZ` | Container Timezone (e.g., `America/New_York`) | `UTC` |

## 🛠️ How it works

1.  **Startup**: The container initializes and immediately attempts one sync cycle to verify credentials and upload today's weight if available.
2.  **Scheduling**: It sets a recurring job for the specified `SYNC_TIME`.
3.  **Sync Logic**:
    - Logs into Renpho Health (AES Encrypted Aggregation API).
    - Fetches the "Daily Calories/Health Manage" data for `Today`.
    - Logs into Garmin Connect.
    - Checks if the data date matches `Today`.
    - Uploads the body composition data to Garmin.

## 👏 Credits

This project stands on the shoulders of giants. Massive thanks to:

- **[ra486/hacs-renpho-health](https://github.com/ra486/hacs-renpho-health)**: For the critical reverse-engineering of the encrypted Renpho Health API logic.
- **[cyberjunky/python-garminconnect](https://github.com/cyberjunky/python-garminconnect)**: For the robust Garmin Connect Python library.

## 📄 License

MIT License. Feel free to fork, mod, and share!
