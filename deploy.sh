#!/bin/bash
# Deployment script for AooServer Discord Bot

echo "🚀 Deploying AooServer Discord Bot..."

# Update code
cd /opt
if [ -d "aooserver-discord-bot" ]; then
    echo "📥 Updating existing repository..."
    cd aooserver-discord-bot
    git pull
else
    echo "📦 Cloning repository..."
    git clone https://github.com/Delta-Zero-Games/aooserver-discord-bot.git
    cd aooserver-discord-bot
fi

# Install/update dependencies
echo "📚 Installing Python dependencies..."
pip3 install -r requirements.txt

# Copy bot file to /opt
echo "📄 Installing bot..."
cp discord-bot.py /opt/discord-aoo-bot.py
chmod +x /opt/discord-aoo-bot.py

# Setup config if not exists
if [ ! -f "/opt/bot-config.json" ]; then
    echo "⚙️ Creating config file..."
    cp bot-config.example.json /opt/bot-config.json
    echo "⚠️ Please edit /opt/bot-config.json with your settings!"
fi

# Install systemd service
echo "🔧 Installing systemd service..."
cat > /etc/systemd/system/discord-bot.service << EOF
[Unit]
Description=Discord AooServer Monitor Bot
After=network.target aooserver.service
Wants=aooserver.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt
ExecStart=/usr/bin/python3 /opt/discord-aoo-bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Reload and restart
echo "🔄 Restarting bot service..."
systemctl daemon-reload
systemctl enable discord-bot
systemctl restart discord-bot

echo "✅ Deployment complete!"
echo ""
echo "📊 Check status with: systemctl status discord-bot"
echo "📜 View logs with: journalctl -u discord-bot -f"
echo "⚙️ Edit config at: /opt/bot-config.json"