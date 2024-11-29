import discord
from discord.ext import commands
from openai import OpenAI
import os
from dotenv import load_dotenv
import logging
from datetime import datetime
import azure.functions as func
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
import re


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
        # Force sync all commands
        await bot.tree.sync()
        logger.info("Slash commands synced successfully")
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

@bot.tree.command(name="sumvideo", description="Summarizes a YouTube video")
async def sumvideo(interaction: discord.Interaction, url: str):
    try:
        await interaction.response.defer()
        
        youtube_regex = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/watch\?v=|youtu\.be\/)([^\s&]+)'
        match = re.search(youtube_regex, url)
        
        if not match:
            await interaction.followup.send("Please provide a valid YouTube URL.")
            return
            
        video_id = match.group(1)
        
        try:
            # Try English transcript first
            transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['en'])
            full_text = " ".join([entry['text'] for entry in transcript])
            
        except (TranscriptsDisabled, NoTranscriptFound):
            await interaction.followup.send("❌ No English transcript available for this video.")
            return
        except Exception as e:
            logger.error(f'Transcript error: {str(e)}')
            await interaction.followup.send("❌ Failed to fetch video transcript.")
            return

        if len(full_text) > 4000:
            full_text = full_text[:4000] + "..."
        
        # Updated prompt for structured summary
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a skilled content analyzer. Provide a structured summary with the following sections:\n- Main Topic\n- Key Points (3-4 bullet points)\n- Key Takeaways"},
                {"role": "user", "content": f"Analyze this video transcript and provide a structured summary:\n{full_text}"}
            ],
            max_tokens=400,
            temperature=0.7
        )
        
        summary = response.choices[0].message.content.strip()
        await interaction.followup.send(f"{interaction.user.mention}, here's a structured summary of the video:\n\n{summary}")
        
    except Exception as e:
        logger.error(f'Error in sumvideo command: {str(e)}', exc_info=True)
        await interaction.followup.send(f"Sorry {interaction.user.mention}, an error occurred: {str(e)}")

@bot.tree.command(name="detailvideo", description="Provides an in-depth analysis with personalized impact assessment")
async def detailvideo(interaction: discord.Interaction, url: str):
    logger.info(f'DetailVideo command received from {interaction.user}')
    
    try:
        await interaction.response.defer()
        
        youtube_regex = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/watch\?v=|youtu\.be\/)([^\s&]+)'
        match = re.search(youtube_regex, url)
        
        if not match:
            await interaction.followup.send("Please provide a valid YouTube URL.")
            return
            
        video_id = match.group(1)
        
        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id)
            full_text = " ".join([entry['text'] for entry in transcript])
            
            if len(full_text) > 4000:
                full_text = full_text[:4000] + "..."
            
            # Detailed analysis prompt
            response = client.chat.completions.create(
                model="gpt-å",
                messages=[
                    {"role": "system", "content": """You are an expert content analyzer with deep understanding of American society in 2024. 
                    Provide a comprehensive analysis with these sections:
                    1. Executive Summary (2-3 sentences)
                    2. Main Topics Covered (bullet points)
                    3. Key Arguments & Evidence
                    4. Notable Quotes or Statistics
                    5. Potential Counterarguments or Limitations
                    6. Practical Applications
                    7. How This Affects You (2024 American Context):
                       - Personal Impact
                       - Community Impact
                       - Action Steps
                       Consider current factors like:
                       - Post-election climate
                       - Economic conditions
                       - Social dynamics
                       - Technology trends
                       - Policy implications
                    8. Related Topics for Further Research
                    
                    Make the "How This Affects You" section particularly engaging and actionable, 
                    considering the current political, economic, and social climate in America."""},
                    {"role": "user", "content": f"Provide a detailed analysis of this video transcript:\n{full_text}"}
                ],
                max_tokens=1000,
                temperature=0.7
            )
            
            analysis = response.choices[0].message.content.strip()
            
            # Split message if it's too long for Discord
            if len(analysis) > 1900:  # Discord has a 2000 character limit
                parts = [analysis[i:i+1900] for i in range(0, len(analysis), 1900)]
                for i, part in enumerate(parts):
                    if i == 0:
                        await interaction.followup.send(f"{interaction.user.mention}, here's a detailed analysis of the video (Part {i+1}/{len(parts)}):\n\n{part}")
                    else:
                        await interaction.followup.send(f"(Part {i+1}/{len(parts)}):\n\n{part}")
            else:
                await interaction.followup.send(f"{interaction.user.mention}, here's a detailed analysis of the video:\n\n{analysis}")
            
        except Exception as e:
            logger.error(f'Error processing transcript: {str(e)}')
            await interaction.followup.send(f"Sorry, I couldn't process the video transcript. Error: {str(e)}")
            
    except Exception as e:
        logger.error(f'Error in detailvideo command: {str(e)}', exc_info=True)
        await interaction.followup.send(f"Sorry {interaction.user.mention}, an error occurred: {str(e)}")

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
