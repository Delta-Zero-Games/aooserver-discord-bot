# AooServer Discord Bot

A Discord Bot that monitors, manages, and dsiplays live status of DZP's connection server for Zero, the low latency voice app

## Features

- 📊 Live status embed that auto-updates every minute
- 🎵 Bot presence showing active users
- 📝 Join/leave notifications
- 🎛️ Server management commands
- 📈 System resource monitoring

## Setup

1. Clone this repository to your server
2. Copy `bot-config.example.json` to `/opt/bot-config.json`
3. Add your Discord bot token and channel IDs
4. Run `deploy.sh` to install

## Commands

- `!status` - Show detailed server status
- `!groups` - List all active groups and users
- `!logs [lines]` - Show recent server logs (admin only)
- `!restart` - Restart AooServer (admin only)

## Configuration

Edit `/opt/bot-config.json`:
- `discord_token`: Your bot token from Discord Developer Portal
- `guild_id`: Your Discord server ID
- `live_status_channel_id`: Channel for the auto-updating status embed
- `notification_channel_id`: (Optional) Channel for join/leave notifications
- `counter_channel_id`: (Optional) Voice channel to show user count