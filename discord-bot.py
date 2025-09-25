#!/usr/bin/env python3
"""
AooServer Discord Bot
Monitors and displays live status of AooServer groups and users
"""

import discord
from discord.ext import commands, tasks
import subprocess
import psutil
import re
import json
import os
from datetime import datetime, timedelta
import asyncio
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('AooBot')

# Load configuration from environment variables or config file
try:
    # Try to load from config.json first
    with open('/opt/bot-config.json', 'r') as f:
        config = json.load(f)
        DISCORD_TOKEN = config['discord_token']
        GUILD_ID = config['guild_id']
        LIVE_STATUS_CHANNEL_ID = config['live_status_channel_id']
        NOTIFICATION_CHANNEL_ID = config.get('notification_channel_id')
        COUNTER_CHANNEL_ID = config.get('counter_channel_id')
except:
    # Fall back to environment variables
    DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
    GUILD_ID = int(os.getenv('GUILD_ID', 0))
    LIVE_STATUS_CHANNEL_ID = int(os.getenv('LIVE_STATUS_CHANNEL_ID', 0))
    NOTIFICATION_CHANNEL_ID = int(os.getenv('NOTIFICATION_CHANNEL_ID', 0)) if os.getenv('NOTIFICATION_CHANNEL_ID') else None
    COUNTER_CHANNEL_ID = int(os.getenv('COUNTER_CHANNEL_ID', 0)) if os.getenv('COUNTER_CHANNEL_ID') else None

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True  # Needed for member tracking

bot = commands.Bot(
    command_prefix='!',
    intents=intents,
    help_command=commands.DefaultHelpCommand(no_category='Commands')
)

class AooMonitor:
    """Monitor the local AooServer"""
    
    def __init__(self):
        self.previous_groups = {}
        self.status_message_id = None
        self.load_state()
    
    def load_state(self):
        """Load saved state from file"""
        try:
            with open('/opt/bot_state.json', 'r') as f:
                state = json.load(f)
                self.status_message_id = state.get('status_message_id')
                self.previous_groups = state.get('previous_groups', {})
        except:
            logger.info("No previous state found, starting fresh")
    
    def save_state(self):
        """Save current state to file"""
        try:
            state = {
                'status_message_id': self.status_message_id,
                'previous_groups': self.previous_groups
            }
            with open('/opt/bot_state.json', 'w') as f:
                json.dump(state, f)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    @staticmethod
    def get_server_status():
        """Check if AooServer is running"""
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'aooserver'],
                capture_output=True,
                text=True
            )
            is_running = result.stdout.strip() == 'active'
            
            # Get PID if running
            pid = None
            if is_running:
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    if 'aooserver' in proc.info['name']:
                        pid = proc.info['pid']
                        break
            
            return is_running, pid
        except:
            return False, None
    
    @staticmethod
    def get_connections():
        """Count active connections to port 10998"""
        try:
            connections = 0
            for conn in psutil.net_connections(kind='tcp'):
                if conn.laddr.port == 10998 and conn.status == 'ESTABLISHED':
                    connections += 1
            return connections
        except:
            return 0
    
    @staticmethod
    def get_server_stats():
        """Get CPU and memory usage"""
        try:
            cpu = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Network stats
            net = psutil.net_io_counters()
            
            # Get AooServer process specific stats
            aoo_cpu = 0
            aoo_mem = 0
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
                if 'aooserver' in proc.info['name']:
                    aoo_cpu = proc.cpu_percent()
                    aoo_mem = proc.memory_info().rss / 1024 / 1024  # MB
                    break
            
            return {
                'cpu': cpu,
                'memory': memory.percent,
                'memory_available_gb': memory.available / 1024 / 1024 / 1024,
                'disk': disk.percent,
                'disk_free_gb': disk.free / 1024 / 1024 / 1024,
                'network_sent_gb': net.bytes_sent / 1024 / 1024 / 1024,
                'network_recv_gb': net.bytes_recv / 1024 / 1024 / 1024,
                'aoo_cpu': aoo_cpu,
                'aoo_memory_mb': aoo_mem
            }
        except Exception as e:
            logger.error(f"Error getting server stats: {e}")
            return {}
    
    def parse_latest_logs(self):
        """Parse AooServer output for group/user info"""
        groups = {}
        try:
            # Get last 500 lines from journal
            result = subprocess.run(
                ['journalctl', '-u', 'aooserver', '-n', '500', '--no-pager'],
                capture_output=True,
                text=True
            )
            
            # Track latest state of each group
            for line in result.stdout.split('\n'):
                # Parse different event types
                # Format: timestamp,id,id2,EventType  param1  param2...
                
                # Handle GroupJoin events: GroupJoin,groupname,username
                if ',GroupJoin,' in line:
                    match = re.search(r',GroupJoin,([^,]+),([^,\s]+)', line)
                    if match:
                        group_name = match.group(1)
                        user_name = match.group(2)
                        if group_name not in groups:
                            groups[group_name] = []
                        if user_name not in groups[group_name]:
                            groups[group_name].append(user_name)
                
                # Handle GroupLeave events: GroupLeave,groupname,username
                elif ',GroupLeave,' in line:
                    match = re.search(r',GroupLeave,([^,]+),([^,\s]+)', line)
                    if match:
                        group_name = match.group(1)
                        user_name = match.group(2)
                        if group_name in groups and user_name in groups[group_name]:
                            groups[group_name].remove(user_name)
                        # Remove empty groups
                        if group_name in groups and len(groups[group_name]) == 0:
                            del groups[group_name]
                
                # Handle UserJoin events (creates user but no group assignment yet)
                elif ',UserJoin,' in line:
                    # UserJoin events are followed by GroupJoin, so we can ignore these
                    # as GroupJoin will handle the user-to-group assignment
                    pass
                
                # Handle UserLeave events (user disconnects entirely)
                elif ',UserLeave,' in line:
                    match = re.search(r',UserLeave,([^,\s]+)', line)
                    if match:
                        user_name = match.group(1)
                        # Remove user from all groups
                        for group_name in list(groups.keys()):
                            if user_name in groups[group_name]:
                                groups[group_name].remove(user_name)
                            # Remove empty groups
                            if len(groups[group_name]) == 0:
                                del groups[group_name]
        
        except Exception as e:
            logger.error(f"Error parsing logs: {e}")
        
        return groups
    
    def get_uptime(self):
        """Get server and service uptime"""
        try:
            # System uptime
            system_uptime = datetime.now() - datetime.fromtimestamp(psutil.boot_time())
            
            # AooServer uptime
            service_uptime = "Unknown"
            result = subprocess.run(
                ['systemctl', 'show', 'aooserver', '--property=ActiveEnterTimestamp'],
                capture_output=True,
                text=True
            )
            if 'ActiveEnterTimestamp=' in result.stdout:
                timestamp_str = result.stdout.split('=')[1].strip()
                if timestamp_str and timestamp_str != 'n/a':
                    # Parse systemd timestamp
                    from dateutil import parser
                    start_time = parser.parse(timestamp_str)
                    service_uptime = datetime.now(start_time.tzinfo) - start_time
            
            return {
                'system': self.format_timedelta(system_uptime),
                'service': self.format_timedelta(service_uptime) if isinstance(service_uptime, timedelta) else service_uptime
            }
        except Exception as e:
            logger.error(f"Error getting uptime: {e}")
            return {'system': 'Unknown', 'service': 'Unknown'}
    
    @staticmethod
    def format_timedelta(td):
        """Format timedelta to readable string"""
        days = td.days
        hours, remainder = divmod(td.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        
        return " ".join(parts) if parts else "< 1m"

# Create monitor instance
monitor = AooMonitor()

@bot.event
async def on_ready():
    """Bot startup event"""
    logger.info(f'{bot.user} has connected to Discord!')
    logger.info(f'Bot is in {len(bot.guilds)} guilds')
    
    # Start background tasks
    update_live_embed.start()
    update_presence.start()
    
    if NOTIFICATION_CHANNEL_ID:
        check_user_changes.start()
    
    if COUNTER_CHANNEL_ID:
        update_counter_channel.start()
    
    logger.info("All background tasks started")

@bot.command(name='status', help='Show AooServer detailed status')
async def server_status(ctx):
    """Show AooServer status"""
    is_running, pid = monitor.get_server_status()
    connections = monitor.get_connections()
    stats = monitor.get_server_stats()
    uptime = monitor.get_uptime()
    groups = monitor.parse_latest_logs()
    
    # Create embed
    embed = discord.Embed(
        title="Zero Connection Server Status",
        color=discord.Color.green() if is_running else discord.Color.red(),
        timestamp=datetime.now()
    )
    
    # Server status
    status_text = f"✅ Online (PID: {pid})" if is_running else "❌ Offline"
    embed.add_field(name="Status", value=status_text, inline=True)
    embed.add_field(name="Connections", value=connections, inline=True)
    embed.add_field(name="Rooms Active", value=len(groups), inline=True)
    
    # Uptime
    embed.add_field(
        name="Uptime", 
        value=f"System: {uptime['system']}\nService: {uptime['service']}", 
        inline=True
    )
    
    # AooServer specific stats
    if stats.get('aoo_cpu') or stats.get('aoo_memory_mb'):
        embed.add_field(
            name="AooServer Usage",
            value=f"CPU: {stats['aoo_cpu']:.1f}%\nRAM: {stats['aoo_memory_mb']:.1f} MB",
            inline=True
        )
    
    # System resources
    embed.add_field(
        name="System Resources",
        value=f"CPU: {stats.get('cpu', 0):.1f}%\nRAM: {stats.get('memory', 0):.1f}%\nDisk: {stats.get('disk', 0):.1f}%",
        inline=True
    )
    
    # Network stats
    if stats.get('network_sent_gb'):
        embed.add_field(
            name="Network Total", 
            value=f"↑ {stats['network_sent_gb']:.2f} GB\n↓ {stats['network_recv_gb']:.2f} GB", 
            inline=False
        )
    
    # embed.set_footer(text="Server: 137.184.8.25:10998")
    
    await ctx.send(embed=embed)

@bot.command(name='groups', help='Show all active rooms and users')
async def show_groups(ctx):
    """Show active rooms and users"""
    groups = monitor.parse_latest_logs()
    
    if not groups:
        embed = discord.Embed(
            title="📡 Active Rooms",
            description="No active rooms at the moment",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
    else:
        # Split into multiple embeds if too many groups
        embeds = []
        embed = discord.Embed(
            title="📡 Active Rooms",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        field_count = 0
        total_users = 0
        
        for group_name, users in groups.items():
            user_count = len(users)
            total_users += user_count
            
            # Discord limit: 25 fields per embed
            if field_count >= 24:
                embed.set_footer(text=f"Showing {field_count} rooms...")
                embeds.append(embed)
                embed = discord.Embed(
                    title="📡 Active Rooms (continued)",
                    color=discord.Color.blue(),
                    timestamp=datetime.now()
                )
                field_count = 0
            
            user_list = ", ".join(users[:5])
            if user_count > 5:
                user_list += f" (+{user_count - 5} more)"
            
            embed.add_field(
                name=f"🎸 {group_name}",
                value=f"**{user_count} user{'s' if user_count != 1 else ''}**\n{user_list if users else 'Empty'}",
                inline=True
            )
            field_count += 1
        
        embed.set_footer(text=f"Total: {len(groups)} rooms, {total_users} users")
        embeds.append(embed)
        
        for e in embeds:
            await ctx.send(embed=e)

@bot.command(name='restart', help='Restart the AooServer (admin only)')
@commands.has_permissions(administrator=True)
async def restart_server(ctx):
    """Restart the AooServer"""
    try:
        await ctx.send("🔄 Restarting AooServer...")
        subprocess.run(['systemctl', 'restart', 'aooserver'], check=True)
        await asyncio.sleep(2)  # Wait for restart
        
        is_running, pid = monitor.get_server_status()
        if is_running:
            await ctx.send(f"✅ AooServer restarted successfully (PID: {pid})")
        else:
            await ctx.send("⚠️ AooServer restarted but status unclear")
    except Exception as e:
        await ctx.send(f"❌ Failed to restart AooServer: {e}")

@bot.command(name='logs', help='Show recent AooServer logs (admin only)')
@commands.has_permissions(administrator=True)
async def show_logs(ctx, lines: int = 20):
    """Show recent AooServer logs"""
    if lines > 50:
        lines = 50  # Limit to prevent spam
    
    try:
        result = subprocess.run(
            ['journalctl', '-u', 'aooserver', '-n', str(lines), '--no-pager'],
            capture_output=True,
            text=True
        )
        
        log_output = result.stdout
        if len(log_output) > 1900:
            log_output = log_output[-1900:]
        
        embed = discord.Embed(
            title="📜 Recent AooServer Logs",
            description=f"```\n{log_output}\n```",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Error fetching logs: {e}")

@tasks.loop(seconds=60)
async def update_live_embed():
    """Update pinned live status message"""
    channel = bot.get_channel(LIVE_STATUS_CHANNEL_ID)
    
    if not channel:
        return
    
    try:
        groups = monitor.parse_latest_logs()
        is_running, _ = monitor.get_server_status()
        connections = monitor.get_connections()
        
        # Create status embed
        embed = discord.Embed(
            title="Zero Live Sessions",
            description="*Auto-updates every minute*",
            color=discord.Color.green() if is_running else discord.Color.red(),
            timestamp=datetime.now()
        )
        
        if not is_running:
            embed.add_field(
                name="⚠️ Server Offline",
                value="The AooServer is currently not running",
                inline=False
            )
        elif not groups:
            embed.add_field(
                name="📡 Server Online",
                value="No active sessions\nWaiting for users to connect...",
                inline=False
            )
        else:
            # Add groups as fields (max 25)
            for i, (group_name, users) in enumerate(list(groups.items())[:25]):
                if i < 24:  # Leave room for stats field
                    user_list = "\n".join([f"• {user}" for user in users[:8]])
                    if len(users) > 8:
                        user_list += f"\n*... +{len(users)-8} more*"
                    
                    embed.add_field(
                        name=f"👥 {group_name} ({len(users)})",
                        value=user_list if users else "*Empty room*",
                        inline=True
                    )
        
        # Footer with stats
        total_users = sum(len(users) for users in groups.values())
        embed.set_footer(
            text=f"📊 {len(groups)} rooms • {total_users} users • {connections} connections"
        )
        
        # Update or create message
        if monitor.status_message_id:
            try:
                message = await channel.fetch_message(monitor.status_message_id)
                await message.edit(embed=embed)
            except discord.NotFound:
                # Message was deleted, create new one
                message = await channel.send(embed=embed)
                await message.pin()
                monitor.status_message_id = message.id
                monitor.save_state()
        else:
            # Create new message
            message = await channel.send(embed=embed)
            
            # Try to pin it
            try:
                await message.pin()
            except discord.Forbidden:
                logger.warning("Cannot pin message - missing Manage Messages permission")
            
            monitor.status_message_id = message.id
            monitor.save_state()
            
    except Exception as e:
        logger.error(f"Error updating live embed: {e}")

@tasks.loop(minutes=2)
async def update_presence():
    """Update bot's Discord presence"""
    try:
        groups = monitor.parse_latest_logs()
        total_users = sum(len(users) for users in groups.values())
        is_running, _ = monitor.get_server_status()
        
        if not is_running:
            await bot.change_presence(
                status=discord.Status.dnd,
                activity=discord.Activity(
                    type=discord.ActivityType.playing,
                    name="⚠️ Server Offline"
                )
            )
        elif total_users > 0:
            await bot.change_presence(
                status=discord.Status.online,
                activity=discord.Activity(
                    type=discord.ActivityType.listening,
                    name=f"{total_users} user{'s' if total_users != 1 else ''} in {len(groups)} room{'s' if len(groups) != 1 else ''}"
                )
            )
        else:
            await bot.change_presence(
                status=discord.Status.idle,
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name="for new connections..."
                )
            )
    except Exception as e:
        logger.error(f"Error updating presence: {e}")

@tasks.loop(seconds=30)
async def check_user_changes():
    """Check for user joins/leaves and post notifications"""
    if not NOTIFICATION_CHANNEL_ID:
        return
    
    channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
    if not channel:
        return
    
    try:
        current_groups = monitor.parse_latest_logs()
        
        # Check for changes
        for group, users in current_groups.items():
            previous_users = monitor.previous_groups.get(group, [])
            
            # Check for joins
            for user in users:
                if user not in previous_users:
                    embed = discord.Embed(
                        description=f"**{user}** joined **{group}**",
                        color=discord.Color.green(),
                        timestamp=datetime.now()
                    )
                    embed.set_author(name="👥 User Joined", icon_url=bot.user.avatar.url if bot.user.avatar else None)
                    await channel.send(embed=embed)
        
        # Check for leaves
        for group, users in monitor.previous_groups.items():
            current_users = current_groups.get(group, [])
            for user in users:
                if user not in current_users:
                    embed = discord.Embed(
                        description=f"**{user}** left **{group}**",
                        color=discord.Color.red(),
                        timestamp=datetime.now()
                    )
                    embed.set_author(name="👋 User Left", icon_url=bot.user.avatar.url if bot.user.avatar else None)
                    await channel.send(embed=embed)
        
        monitor.previous_groups = current_groups.copy()
        monitor.save_state()
        
    except Exception as e:
        logger.error(f"Error checking user changes: {e}")

@tasks.loop(minutes=5)
async def update_counter_channel():
    """Update voice channel name with user count"""
    if not COUNTER_CHANNEL_ID:
        return
    
    try:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return
        
        channel = guild.get_channel(COUNTER_CHANNEL_ID)
        if not channel:
            return
        
        groups = monitor.parse_latest_logs()
        total_users = sum(len(users) for users in groups.values())
        
        new_name = f"👥 Users Online: {total_users}"
        
        # Only update if name changed (rate limit prevention)
        if channel.name != new_name:
            await channel.edit(name=new_name)
            logger.info(f"Updated counter channel: {new_name}")
    except Exception as e:
        logger.error(f"Error updating counter channel: {e}")

@bot.event
async def on_command_error(ctx, error):
    """Handle command errors"""
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")
    elif isinstance(error, commands.CommandNotFound):
        pass  # Ignore unknown commands
    else:
        logger.error(f"Command error: {error}")
        await ctx.send(f"❌ An error occurred: {error}")

# Run the bot
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("No Discord token found! Set DISCORD_TOKEN environment variable or create /opt/bot-config.json")
        exit(1)
    
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")