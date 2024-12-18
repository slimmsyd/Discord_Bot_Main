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
from aiohttp import web
import asyncio
from io import BytesIO
import requests
from bs4 import BeautifulSoup
import http.client
import socket

# Add health check routes
async def health_check(request):
    return web.Response(text="Healthy", status=200)

# Create web app
app = web.Application()
app.router.add_get('/health', health_check)
app.router.add_get('/', health_check)

# Modified run function
async def run_bot_and_server():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8000)
    await site.start()
    
    # Rest of your existing bot setup code...
    try:
        await bot.start(token)
    except Exception as e:
        logger.critical(f'Failed to start bot: {str(e)}', exc_info=True)
    finally:
        await runner.cleanup()

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

# Add these configurations after your imports
# Increase timeout for HTTP operations
socket.setdefaulttimeout(30)
http.client._MAXHEADERS = 1000

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
            model="gpt-4o-mini",
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
    # Store references early
    channel = interaction.channel
    user = interaction.user
    
    try:
        try:
            # Try to acknowledge the interaction immediately
            await interaction.response.defer(thinking=True)
            response_method = interaction.followup.send
        except discord.NotFound:
            # If interaction expired, fall back to regular channel messages
            logger.info("Interaction expired, falling back to channel messages")
            await channel.send(f"{user.mention} Processing your request...")
            response_method = channel.send
        
        # Rest of your existing code, but replace all interaction.followup.send with response_method
        youtube_regex = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:watch\?v=|live\/|embed\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})'
        match = re.search(youtube_regex, url)
        
        if not match:
            await response_method("Please provide a valid YouTube URL.")
            return
            
        video_id = match.group(1)
        
        try:
            # Validate video ID exists before attempting transcript
            try:
                validate_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
                validation_response = requests.get(validate_url)
                if validation_response.status_code != 200:
                    await response_method("❌ This video appears to be unavailable or private.")
                    return
                    
                video_info = validation_response.json()
                logger.info(f"Processing video: {video_info.get('title', 'Unknown Title')}")
                
            except Exception as e:
                logger.error(f"Error validating video: {str(e)}")
                await response_method("❌ Error validating video URL.")
                return

            # Initialize transcript variable
            transcript = None
            
            # Method 1: Direct fetch with multiple languages and proxy handling
            if not transcript:
                languages = ['en', 'en-US', 'en-GB', 'auto']
                proxies = {
                    'http': None,
                    'https': None
                }
                for lang in languages:
                    try:
                        # Try with default settings
                        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=[lang])
                        break
                    except Exception as e1:
                        try:
                            # Try with proxies disabled
                            transcript = YouTubeTranscriptApi.get_transcript(
                                video_id, 
                                languages=[lang],
                                proxies=proxies
                            )
                            break
                        except Exception as e2:
                            logger.error(f"Failed attempt for {lang}: {str(e1)} | {str(e2)}")
                            continue

            # Method 2: Try listing available transcripts
            if not transcript:
                try:
                    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                    
                    # Try different methods in sequence
                    methods = [
                        lambda: transcript_list.find_generated_transcript(['en']),
                        lambda: transcript_list.find_generated_transcript(['en-US', 'en-GB']),
                        lambda: transcript_list.find_manually_created_transcript(['en']),
                        lambda: next((t for t in transcript_list.manual_transcripts), None),
                        lambda: next((t for t in transcript_list.generated_transcripts), None),
                    ]
                    
                    for method_num, method in enumerate(methods, 1):
                        try:
                            logger.info(f"Trying transcript method {method_num}")
                            result = method()
                            if result:
                                transcript = result.fetch()
                                logger.info(f"Method {method_num} succeeded")
                                break
                        except Exception as e:
                            logger.error(f"Method {method_num} failed: {str(e)}")
                            continue

                except Exception as e:
                    logger.error(f"Failed to list transcripts: {str(e)}")

            # Method 3: Try any available transcript and translate
            if not transcript:
                try:
                    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                    available = transcript_list.manual_transcripts + transcript_list.generated_transcripts
                    if available:
                        first_transcript = available[0]
                        logger.info(f"Found transcript in {first_transcript.language_code}, translating to English")
                        transcript = first_transcript.translate('en').fetch()
                except Exception as e:
                    logger.error(f"Translation attempt failed: {str(e)}")

            # Process transcript if we got it
            if transcript:
                # Convert to list format if it's not already
                if not isinstance(transcript, list):
                    transcript = transcript.fetch()
                
                full_text = " ".join([entry['text'] for entry in transcript])
                
                if len(full_text) > 4000:
                    full_text = full_text[:4000] + "..."
                
                # Create the analysis
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": """Analyze the content in this structured format:
                        1. CORE CONCEPT (2-3 sentences)
                        2. BREAKDOWN (key points)
                        3. IMPLICATIONS
                        4. CRITICAL ANALYSIS
                        5. FUTURE OUTLOOK
                        
                        Keep each section brief and concise."""},
                        {"role": "user", "content": f"Analyze this video transcript:\n{full_text}"}
                    ],
                    max_tokens=300,
                    temperature=0.7
                )
                
                analysis = response.choices[0].message.content.strip()
                await response_method(analysis)
            else:
                await response_method("❌ No transcript available for this video. This might be because:\n" +
                                    "• Subtitles are disabled\n" +
                                    "• The video is private or age-restricted\n" +
                                    "• No auto-generated captions are available\n" +
                                    "• The video is too new and captions haven't been processed yet")

        except Exception as e:
            logger.error(f'Transcript processing error: {str(e)}')
            await response_method(f"❌ Error processing video transcript: {str(e)}")
            
    except Exception as e:
        logger.error(f'Error in sumvideo command: {str(e)}', exc_info=True)
        try:
            await response_method(f"Sorry, an error occurred: {str(e)}")
        except:
            await channel.send(f"Sorry, an error occurred: {str(e)}")

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
                model="gpt-4o-mini",
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

@bot.tree.command(name="meme", description="Generates a meme based on recent conversation")
async def meme(interaction: discord.Interaction):
    logger.info(f'Meme command received from {interaction.user} in {interaction.guild.name}/{interaction.channel.name}')
    
    try:
        # Defer the response since this will take time
        await interaction.response.defer()
        
        # Fetch last 3 messages
        messages = [message async for message in interaction.channel.history(limit=3)]
        messages.reverse()  # Put in chronological order
        
        # Format messages for context
        conversation = "\n".join([f"{msg.author.name}: {msg.content}" for msg in messages])
        
        # First, get a meme concept from GPT
        concept_response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": """You are a meme expert. Based on the conversation, 
                create a funny meme concept. Create a detailed but concise image description that 
                DALL-E can use to generate a humorous meme-style image. Add a short, witty caption.
                
                Format your response exactly like this:
                IMAGE: [detailed image description]
                CAPTION: [short witty text]"""},
                {"role": "user", "content": f"Create a meme based on this conversation:\n{conversation}"}
            ],
            max_tokens=150,
            temperature=0.9
        )
        
        meme_concept = concept_response.choices[0].message.content.strip()
        image_desc = ""
        caption = ""
        
        # Parse the response
        for line in meme_concept.split('\n'):
            if line.startswith('IMAGE:'):
                image_desc = line.replace('IMAGE:', '').strip()
            elif line.startswith('CAPTION:'):
                caption = line.replace('CAPTION:', '').strip()
        
        # Generate image using DALL-E
        image_response = client.images.generate(
            model="dall-e-3",
            prompt=f"Create a meme-style image: {image_desc}. Make it funny and suitable for a meme. Use bold, clear visuals typical of internet memes.",
            n=1,
            size="1024x1024",
            quality="standard"
        )
        
        # Get the image URL
        image_url = image_response.data[0].url
        
        # Download the image
        image_response = requests.get(image_url)
        image_data = BytesIO(image_response.content)
        
        # Send the meme
        await interaction.followup.send(
            content=f"**{caption}**\n*Generated based on your conversation*",
            file=discord.File(fp=image_data, filename='meme.png')
        )
        
    except Exception as e:
        logger.error(f'Error in meme command: {str(e)}', exc_info=True)
        await interaction.followup.send(
            f"Sorry {interaction.user.mention}, I couldn't generate the meme. Error: {str(e)}"
        )

@bot.tree.command(name="dearoracle", description="Ask the Street Oracle any question for some street-wise wisdom")
async def dearoracle(interaction: discord.Interaction, question: str):
    logger.info(f'Street Oracle question received from {interaction.user}: {question}')
    
    try:
        await interaction.response.defer()
        
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": """You are the Street Oracle, a wise but cool advisor who speaks in 
                casual, urban style, new york slang, Gods of the streets, slang . Always start your response with "Lil homie," and maintain a friendly, 
                street-smart tone. Use casual language but give genuinely thoughtful advice. Keep your responses 
                relatively concise but meaningful."""},
                {"role": "user", "content": question}
            ],
            max_tokens=150,
            temperature=0.7
        )
        
        oracle_wisdom = response.choices[0].message.content.strip()
        await interaction.followup.send(f"🔮 {oracle_wisdom}")
        
    except Exception as e:
        logger.error(f'Error in dearoracle command: {str(e)}', exc_info=True)
        await interaction.followup.send(
            f"Yo {interaction.user.mention}, my crystal ball's acting up right now. Try again later! Error: {str(e)}"
        )
        
@bot.tree.command(name="motivate", description="Ask the Street Oracle to motivate you")
async def motivate(interaction: discord.Interaction):
    logger.info(f'Street Oracle question received from {interaction.user}')
    
    try:
        await interaction.response.defer()
        
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": """You are the Street Oracle, with a love of stoicism and the art of living well. Most of you thoughts are based on the early stoics and the early Greek philosophers. Mainly Heraclitus, Epictetus, and Seneca. Be diverse in your thoughts and ideas. 
                . Always start your response with "Young God," and maintain a friendly, 
                street-smart tone. Use casual language but give genuinely thoughtful advice. Keep your responses 
                relatively concise but meaningful."""},
                {"role": "user", "content": "Give me a aphorism and or quote of wisdom  "}
            ],
            max_tokens=150,
            temperature=0.7
        )
        
        oracle_wisdom = response.choices[0].message.content.strip()
        await interaction.followup.send(f"🔮 {oracle_wisdom}")
        
    except Exception as e:
        logger.error(f'Error in dearoracle command: {str(e)}', exc_info=True)
        await interaction.followup.send(
            f"Yo {interaction.user.mention}, my crystal ball's acting up right now. Try again later! Error: {str(e)}"
        )
        
@bot.tree.command(name="finnasumthisup", description="Street Oracle breaks down an article for you")
async def finnasumthisup(interaction: discord.Interaction, url: str):
    logger.info(f'Article summary requested by {interaction.user}: {url}')
    
    try:
        await interaction.response.defer()
        
        # Fetch the article content
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract text from the article
        article_text = ""
        
        # Look for common article containers
        article_containers = soup.find_all(['article', 'div'], class_=re.compile(r'article|content|story|post'))
        for container in article_containers:
            paragraphs = container.find_all('p')
            article_text += ' '.join([p.get_text().strip() for p in paragraphs])
        
        if not article_text:
            # Fallback to all paragraphs if no article container found
            paragraphs = soup.find_all('p')
            article_text = ' '.join([p.get_text().strip() for p in paragraphs])
        
        # Get Street Oracle to summarize
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": """You are the Street Oracle, breaking down complex articles in 
                street-smart language with New York slang and urban wisdom. Structure your response like this:
                
                "Ay yo lil homie, peep game on this article right quick:
                
                [Break down main points using street slang and urban metaphors]
                
                Bottom line: [key takeaway with street flavor]"
                
                Keep it real, informative, and entertaining. Use authentic street/urban language."""},
                {"role": "user", "content": f"Break down this article in your style:\n\n{article_text}"}
            ],
            max_tokens=400,
            temperature=0.7
        )
        
        summary = response.choices[0].message.content.strip()
        
        # Send the summary with some style
        await interaction.followup.send(
            f"🗞️ **Street Oracle Article Breakdown** 🔮\n\n{summary}\n\n*Original article: {url}*"
        )
        
    except Exception as e:
        logger.error(f'Error in finnasumthisup command: {str(e)}', exc_info=True)
        await interaction.followup.send(
            f"Ay yo {interaction.user.mention}, my bad fam! Couldn't grab that article. "
            f"Make sure that link is straight and try again later! Error: {str(e)}"
        )

@bot.tree.command(name="fryemup", description="Street Oracle roasts based on recent messages")
async def fryemup(interaction: discord.Interaction):
    logger.info(f'Roast command received from {interaction.user} in {interaction.guild.name}/{interaction.channel.name}')
    
    try:
        await interaction.response.defer()
        
        # Fetch last 5 messages for context
        messages = [message async for message in interaction.channel.history(limit=5)]
        messages.reverse()  # Put in chronological order
        
        # Format messages for context
        conversation = "\n".join([f"{msg.author.name}: {msg.content}" for msg in messages])
        
        # Get the Street Oracle to deliver a roast
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": """You are the Street Oracle, now in roast mode. You're delivering 
                a humorous, witty roast using New York street slang and urban wisdom. Your roasts should be:
                - Funny but not truly mean-spirited
                - Creative with street metaphors
                - Using authentic urban slang
                - Always start with "I finna fry ya ass up, if you dont getcho ol'"
                - Always end with "lil homie"
                - Reference specific things from the conversation
                - Keep it playful and entertaining
                
                Example style:
                "I finna fry ya ass up, if you dont getcho ol' pokemon card collecting, 
                hot cheeto fingers, calculator watching, math class failing self somewhere else lil homie"
                
                Make it funny and creative, but keep it relatively clean and not actually hurtful."""},
                {"role": "user", "content": f"Create a roast based on this conversation:\n{conversation}"}
            ],
            max_tokens=200,
            temperature=0.9
        )
        
        roast = response.choices[0].message.content.strip()
        
        # Send the roast with some style
        await interaction.followup.send(
            f"🔥 **Street Oracle Roast** 🔥\n\n{roast}"
        )
        
    except Exception as e:
        logger.error(f'Error in fryemup command: {str(e)}', exc_info=True)
        await interaction.followup.send(
            f"Ay yo {interaction.user.mention}, my bad fam! The roast ain't cooking right now. "
            f"Try again later when the heat back on! Error: {str(e)}"
        )

@bot.event
async def on_command_error(ctx, error):
    logger.error(f'Command error: {str(error)}', exc_info=True)
    await ctx.send(f"An error occurred: {str(error)}")

@bot.event
async def on_message(message):
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return
        
    # Check if this is a reply to our bot
    if message.reference and message.reference.resolved:
        referenced_message = message.reference.resolved
        # Only respond if it's replying to a fryemup command
        if (referenced_message.author == bot.user and 
            "🔥 **Street Oracle Roast**" in referenced_message.content):
            logger.info(f'Roast was replied to by {message.author} saying: {message.content}')
            
            try:
                response = client.chat.completions.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": """You are the Street Oracle in comeback mode. Your job is to 
                        create a comeback roast that specifically references what the person said in their reply.
                        Use their own words against them in a clever way. Format must be EXACTLY:
                        "im on ya ass boi, you look like a [FIRST_ROAST based on their reply] like shit boi, like a mf [SECOND_ROAST based on their reply]"
                        
                        Example:
                        If they say "your roasts are weak", you might respond:
                        "im on ya ass boi, you look like a dictionary reading roast critic like shit boi, like a mf comedy show heckler from the dollar seats"
                        
                        Make it:
                        - Directly reference their reply
                        - Use street slang
                        - Be creative and funny
                        - Keep it playful not mean"""},
                        {"role": "user", "content": f"Create a comeback roast based on their reply: {message.content}"}
                    ],
                    max_tokens=150,
                    temperature=0.9
                )
                
                comeback = response.choices[0].message.content.strip()
                await message.reply(f"🔥 {comeback}")
                
            except Exception as e:
                logger.error(f'Error in roast comeback: {str(e)}', exc_info=True)
                await message.channel.send(
                    f"Ay yo, my comeback game ain't working right now, but you still look suspect {message.author.mention} 😤"
                )
    
    # Process commands after handling the reply
    await bot.process_commands(message)

# Run the bot
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run_bot_and_server())
    except KeyboardInterrupt:
        loop.run_until_complete(bot.close())
    finally:
        loop.close()
