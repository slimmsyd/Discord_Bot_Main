import discord
from discord.ext import commands
from openai import OpenAI
import os
from dotenv import load_dotenv
import logging
from datetime import datetime
import azure.functions as func


# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('discord_bot')

# Load .env file
load_dotenv()

# Token validation
token = os.getenv('DISCORD_BOT_TOKEN')
if token is None:
    logger.error("No Discord token found. Make sure DISCORD_BOT_TOKEN is set in your .env file")
    raise ValueError("No Discord token found")

# OpenAI setup
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
if not client.api_key:
    logger.error("No OpenAI API key found. Make sure OPENAI_API_KEY is set in your .env file")
    raise ValueError("No OpenAI API key found")

# Discord bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix='/', intents=intents)

@bot.event
async def on_ready():
    logger.info(f'Bot logged in as {bot.user.name} (ID: {bot.user.id})')
    logger.info(f'Connected to {len(bot.guilds)} guilds')
    try:
        synced = await bot.tree.sync()
        logger.info(f'Slash commands synced: {len(synced)}')
    except Exception as e:
        logger.error(f'Failed to sync slash commands: {e}')
    for guild in bot.guilds:
        logger.info(f'Connected to guild: {guild.name} (ID: {guild.id})')

@bot.tree.command(name="summarize", description="Summarizes the last 20 messages in the channel")
async def summarize(interaction: discord.Interaction):
    logger.info(f'Summarize command received from {interaction.user} in {interaction.guild.name}/{interaction.channel.name}')
    
    try:
        # Defer the response since summarization might take time
        await interaction.response.defer()
        
        # Fetch messages
        messages = [message async for message in interaction.channel.history(limit=20)]
        logger.info(f'Fetched {len(messages)} messages for summarization')
        
        # Format messages
        thread_content = "\n".join([f"{msg.author.name}: {msg.content}" for msg in reversed(messages)])
        
        # OpenAI API call
        start_time = datetime.now()
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that summarizes conversations."},
                {"role": "user", "content": f"Please summarize this conversation:\n{thread_content}"}
            ],
            max_tokens=150,
            temperature=0.7
        )
        
        # Send summary
        summary = response.choices[0].message.content.strip()
        await interaction.followup.send(f"{interaction.user.mention}, here's a summary of the last 20 messages:\n{summary}")
        
    except Exception as e:
        logger.error(f'Error in summarize command: {str(e)}', exc_info=True)
        await interaction.followup.send(f"Sorry {interaction.user.mention}, I couldn't summarize the messages. Error: {str(e)}")

@bot.event
async def on_command_error(ctx, error):
    logger.error(f'Command error: {str(error)}', exc_info=True)
    await ctx.send(f"An error occurred: {str(error)}")

# Run the bot
try:
    logger.info('Starting bot...')
    bot.run(token)
except Exception as e:
    logger.critical(f'Failed to start bot: {str(e)}', exc_info=True)

if __name__ == "__main__":
    bot.run(token)
