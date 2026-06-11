import discord
from discord.ext import commands
from openai import OpenAI
import os
from dotenv import load_dotenv
import logging
from datetime import datetime, timezone
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
import re
from aiohttp import web
import asyncio
import requests
from bs4 import BeautifulSoup
import http.client
import socket
import httpx
import time
from channel_finder import find_channels
from discord_utils import split_for_discord
from persona import STREET_ORACLE_SYSTEM, build_messages
from conversation_memory import ConversationMemory, make_key
from member_export import build_member_csv, member_export_filename
from join_tracker import diff_invite_uses, JoinLog, LeaveLog
from growth_stats import growth_windows, join_cohorts, top_inviters, recent_leavers
from survey_ai import generate_questions
from survey_store import SurveyStore, build_survey_csv, survey_message_text
import io
import uuid

SOLANA_ADDRESS_REGEX = r'^[1-9A-HJ-NP-Za-km-z]{32,44}$'  # Solana addresses are base58
BASE_ADDRESS_REGEX = r'^0x[a-fA-F0-9]{40}$'  # Base uses Ethereum-style addresses

# Model used for all AI calls (DeepSeek's OpenAI-compatible chat model)
AI_MODEL = "deepseek-chat"

# Add health check routes (keeps the host's healthcheck happy; not required by Discord)
async def health_check(request):
    return web.Response(text="Healthy", status=200)

# Create web app
app = web.Application()
app.router.add_get('/health', health_check)
app.router.add_get('/', health_check)

# Run the health server + the Discord gateway bot together
async def run_bot_and_server():
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv('PORT', '8000'))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    logger.info(f"Web server started successfully on port {port}")

    try:
        await bot.start(token)
    except Exception as e:
        logger.critical(f'Failed to start bot: {str(e)}', exc_info=True)
        raise
    finally:
        await runner.cleanup()

# Set up logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
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

# DeepSeek setup (OpenAI-compatible API)
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
if not DEEPSEEK_API_KEY:
    logger.error("No DeepSeek API key found. Make sure DEEPSEEK_API_KEY is set in your .env file")
    raise ValueError("No DeepSeek API key found")

deepseek_client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
    http_client=httpx.Client(
        timeout=60,
        follow_redirects=True
    )
)

# Discord bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
# Server Members is a PRIVILEGED intent — required to read the full member list
# and join dates for /exportmembers. You MUST enable "Server Members Intent" in
# the Discord Developer Portal (Bot tab) or the bot will fail to log in.
intents.members = True
bot = commands.Bot(command_prefix='/', intents=intents)

# Ephemeral per-(channel, user) conversation memory for the @mention agent.
oracle_memory = ConversationMemory()

# Owner IDs allowed to run restricted commands (comma/space separated in env).
OWNER_IDS = {
    int(x) for x in os.getenv('OWNER_IDS', '').replace(',', ' ').split() if x.strip().isdigit()
}

# Join tracking: persisted record of how each member joined, plus an in-memory
# cache of each guild's invite use-counts so on_member_join can diff them.
join_log = JoinLog(os.getenv('JOIN_LOG_PATH', 'join_log.json'))
leave_log = LeaveLog(os.getenv('LEAVE_LOG_PATH', 'leave_log.json'))
guild_invite_uses = {}  # guild_id -> {invite_code: uses}

# AI-generated surveys + their responses, plus a guard so we register each
# survey's persistent "Take the survey" button view only once per process.
survey_store = SurveyStore(os.getenv('SURVEY_STORE_PATH', 'surveys.json'))
_registered_survey_views = set()


def is_owner_or_admin(interaction):
    """True if the invoker is a configured owner or has server Administrator."""
    if interaction.user.id in OWNER_IDS:
        return True
    perms = getattr(interaction.user, 'guild_permissions', None)
    return bool(perms and perms.administrator)


async def cache_guild_invites(guild):
    """Snapshot a guild's invite use-counts. Needs 'Manage Server' permission."""
    try:
        invites = await guild.invites()
        guild_invite_uses[guild.id] = {inv.code: (inv.uses or 0) for inv in invites}
    except discord.Forbidden:
        logger.warning(
            f"Missing 'Manage Server' permission to read invites in {guild.name}; "
            "join tracking disabled for this guild."
        )
    except Exception as e:
        logger.error(f"Failed to cache invites for {guild.name}: {e}")

# Increase timeout for HTTP operations
socket.setdefaulttimeout(30)
http.client._MAXHEADERS = 1000


@bot.event
async def on_ready():
    logger.info(f"""
=== Bot Started ===
Name: {bot.user.name}
ID: {bot.user.id}
Servers Connected: {len(bot.guilds)}
Server List:
{chr(10).join([f'- {guild.name} (ID: {guild.id})' for guild in bot.guilds])}
=================""")

    try:
        # Force sync all commands
        await bot.tree.sync()
        logger.info("Slash commands synced successfully")
    except Exception as e:
        logger.error(f'Failed to sync slash commands: {e}')
    for guild in bot.guilds:
        logger.info(f'Connected to guild: {guild.name} (ID: {guild.id})')
        # Prime the invite cache so the first join after startup can be attributed.
        await cache_guild_invites(guild)

    # Re-attach the persistent "Take the survey" button to surveys that are
    # still active, so their buttons keep working after a restart.
    for s in survey_store.list_active():
        if s["id"] not in _registered_survey_views:
            bot.add_view(SurveyTakeView(s["id"]))
            _registered_survey_views.add(s["id"])


@bot.event
async def on_invite_create(invite):
    """Keep the invite-use cache fresh as new invites are made."""
    guild_invite_uses.setdefault(invite.guild.id, {})[invite.code] = invite.uses or 0


@bot.event
async def on_invite_delete(invite):
    """Drop deleted invites from the cache so they don't linger."""
    cache = guild_invite_uses.get(invite.guild.id)
    if cache is not None:
        cache.pop(invite.code, None)


@bot.event
async def on_member_join(member):
    """Attribute a join to an invite by diffing use-counts against the cache."""
    guild = member.guild
    before = guild_invite_uses.get(guild.id, {})
    try:
        invites = await guild.invites()
    except discord.Forbidden:
        invites = []
    except Exception as e:
        logger.error(f"Failed to fetch invites on join in {guild.name}: {e}")
        invites = []

    after = {inv.code: (inv.uses or 0) for inv in invites}
    used_code = diff_invite_uses(before, after)
    if after:  # only overwrite the cache when we actually read invites
        guild_invite_uses[guild.id] = after

    record = {
        "method": "unknown",
        "joined_at": member.joined_at.isoformat() if member.joined_at else "",
    }
    if used_code:
        record["method"] = "invite"
        record["invite_code"] = used_code
        inv = next((i for i in invites if i.code == used_code), None)
        if inv and inv.inviter:
            record["inviter_id"] = str(inv.inviter.id)
            record["inviter_tag"] = str(inv.inviter)

    try:
        join_log.record(member.id, record)
    except Exception as e:
        logger.error(f"Failed to persist join record for {member.id}: {e}")


@bot.event
async def on_member_remove(member):
    """Log a departure so /growth can compute churn and net growth."""
    event = {
        "user_id": str(member.id),
        "username": str(member),
        "left_at": datetime.now(timezone.utc).isoformat(),
        # member.joined_at is available while the member is still cached, letting
        # us record how long they'd been around before leaving.
        "joined_at": member.joined_at.isoformat() if member.joined_at else "",
    }
    try:
        leave_log.record(event)
    except Exception as e:
        logger.error(f"Failed to persist leave event for {member.id}: {e}")


class CryptoTools:
    @staticmethod
    async def get_dex_price(contract_address: str, network: str = "ethereum") -> str:
        """Fetches price for a token using DEX data."""
        try:
            # Define API endpoints for different networks
            dex_apis = {
                "ethereum": "https://api.1inch.io/v5.0/1",
                "bsc": "https://api.1inch.io/v5.0/56",
                "polygon": "https://api.1inch.io/v5.0/137",
                "base": "https://api.1inch.io/v5.0/8453"  # Added Base chain
            }

            if network.lower() not in dex_apis:
                return f"Unsupported network: {network}"

            # Standard stable pairs for price checking
            base_tokens = {
                "ethereum": "0xdAC17F958D2ee523a2206206994597C13D831ec7",  # USDT
                "bsc": "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",      # BUSD
                "polygon": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",   # USDT
                "base": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb"      # USDbC
            }

            # Get quote from 1inch API
            quote_url = f"{dex_apis[network.lower()]}/quote"
            params = {
                "fromTokenAddress": contract_address,
                "toTokenAddress": base_tokens[network.lower()],
                "amount": "1000000000000000000"  # 1 token in wei
            }

            response = requests.get(quote_url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                # Calculate price in USD
                price = float(data['toTokenAmount']) / float(data['fromTokenAmount'])
                return f"The current DEX price is ${price:.8f} USD"
            else:
                return "Could not fetch DEX price. Token might not have enough liquidity."

        except Exception as e:
            logger.error(f"DEX price fetch error: {str(e)}")
            return f"Error fetching DEX price: {str(e)}"

    @staticmethod
    async def get_solana_price(address: str) -> str:
        """Fetches price for a Solana token using Jupiter API and cross-references metadata."""
        try:
            # Try multiple sources for token metadata
            token_name = "Unknown Token"
            token_symbol = ""

            # 1. Try Birdeye API for both price and metadata
            birdeye_price_url = "https://public-api.birdeye.so/defi/price"
            birdeye_token_url = "https://public-api.birdeye.so/public/token"
            headers = {
                "X-API-KEY": "a9907fc664764811855e98e0835862a3",
                "x-chain": "solana",
                "accept": "application/json"
            }

            # Get token metadata from Birdeye
            token_params = {"address": address}
            token_response = requests.get(birdeye_token_url, headers=headers, params=token_params, timeout=10)

            if token_response.status_code == 200:
                token_data = token_response.json()
                if token_data.get("success") and token_data.get("data"):
                    metadata = token_data["data"]
                    token_name = metadata.get("name", "Unknown Token")
                    token_symbol = metadata.get("symbol", "")

            # If still unknown, try Jupiter's token list
            if token_name == "Unknown Token":
                metadata_url = "https://token.jup.ag/all"
                metadata_response = requests.get(metadata_url, timeout=10)

                if metadata_response.status_code == 200:
                    tokens = metadata_response.json()
                    for token in tokens:
                        if token.get("address") == address:
                            token_name = token.get("name", "Unknown Token")
                            token_symbol = token.get("symbol", "")
                            break

            # Get price from Birdeye
            price_params = {"address": address}
            price_response = requests.get(birdeye_price_url, headers=headers, params=price_params, timeout=10)
            logger.info(f"Birdeye API Response: {price_response.status_code} - {price_response.text[:200]}")

            if price_response.status_code == 200:
                price_data = price_response.json()
                if price_data.get("success") and price_data.get("data"):
                    price = float(price_data["data"].get("value", 0))
                    price_change = price_data["data"].get("priceChange24h", 0)
                    volume_24h = price_data["data"].get("volume24h", 0)

                    # Format token info
                    token_info = f"{token_name}"
                    if token_symbol:
                        token_info += f" ({token_symbol})"

                    # Add address for unknown tokens
                    if token_name == "Unknown Token":
                        token_info += f"\nAddress: {address}"

                    # Build response with additional info
                    response = f"Token: {token_info}\n"
                    response += f"Current Price: ${price:.8f} USD\n"
                    response += f"24h Change: {price_change:.2f}%\n"
                    response += f"24h Volume: ${volume_24h:,.2f}"

                    return response

                return "Could not find price data for this Solana token"
            else:
                return f"Birdeye API returned status code {price_response.status_code}"

        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {str(e)}")
            return f"Failed to connect to API: {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return f"Error processing price: {str(e)}"

    @staticmethod
    async def get_crypto_price(crypto: str, currency: str = "usd") -> str:
        """Fetches the current price of a cryptocurrency."""
        logger.info(f"Fetching crypto price for {crypto} in {currency}")

        # Check for Solana address first
        if re.match(SOLANA_ADDRESS_REGEX, crypto):
            return await CryptoTools.get_solana_price(crypto)

        # Check for Base address
        if re.match(BASE_ADDRESS_REGEX, crypto):
            # Try Base network first
            base_price = await CryptoTools.get_dex_price(crypto, "base")
            if "USD" in base_price:
                return f"[Base Chain] {base_price}"
            return await CryptoTools.get_dex_price(crypto, "ethereum")  # Fallback

        # Check if input is a contract address (0x...)
        if crypto.startswith("0x") and len(crypto) == 42:
            # Try networks in order
            networks = ["ethereum", "bsc", "polygon", "base"]
            for network in networks:
                price_info = await CryptoTools.get_dex_price(crypto, network)
                if "current DEX price" in price_info:
                    return f"[{network.upper()}] {price_info}"
            return "Could not find price for this token on major DEXes"

        try:
            # First try direct CoinGecko API call
            direct_response = requests.get(
                f"https://api.coingecko.com/api/v3/simple/price",
                params={"ids": crypto.lower(), "vs_currencies": currency.lower()},
                timeout=10
            )

            if direct_response.status_code == 200 and crypto.lower() in direct_response.json():
                price = direct_response.json()[crypto.lower()][currency.lower()]
                return f"The current price of {crypto.capitalize()} is {price:,.2f} {currency.upper()}"

            # If direct call fails, try common mappings
            crypto_mapping = {
                "bitcoin": "bitcoin",
                "btc": "bitcoin",
                "ethereum": "ethereum",
                "eth": "ethereum",
                "matic": "polygon",
                "polygon": "polygon"
                # Add more mappings as needed
            }

            crypto_id = crypto_mapping.get(crypto.lower())

            if crypto_id:
                response = requests.get(
                    f"https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": crypto_id, "vs_currencies": currency.lower()},
                    timeout=10
                )
                response.raise_for_status()

                price_data = response.json()
                if crypto_id in price_data:
                    price = price_data[crypto_id][currency.lower()]
                    return f"The current price of {crypto.capitalize()} is {price:,.2f} {currency.upper()}"

            # If both attempts fail, search CoinGecko's coin list
            search_response = requests.get(
                "https://api.coingecko.com/api/v3/search",
                params={"query": crypto},
                timeout=10
            )

            if search_response.status_code == 200:
                search_data = search_response.json()
                if search_data.get("coins"):
                    # Get the first (most relevant) result
                    coin = search_data["coins"][0]
                    coin_id = coin["id"]

                    # Fetch price for found coin
                    final_response = requests.get(
                        f"https://api.coingecko.com/api/v3/simple/price",
                        params={"ids": coin_id, "vs_currencies": currency.lower()},
                        timeout=10
                    )

                    if final_response.status_code == 200:
                        price_data = final_response.json()
                        if coin_id in price_data:
                            price = price_data[coin_id][currency.lower()]
                            return f"The current price of {coin['name']} ({coin['symbol'].upper()}) is {price:,.2f} {currency.upper()}"

            return f"Sorry, couldn't find price data for {crypto}"

        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {str(e)}")
            return f"Failed to fetch price data: {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return f"An unexpected error occurred: {str(e)}"


async def send_oracle_reply(interaction, content):
    """Send the Oracle's reply, splitting into multiple messages if it would
    exceed Discord's 2000-char limit."""
    for chunk in split_for_discord(f"🔮 {content}"):
        await interaction.followup.send(chunk)


@bot.tree.command(name="dearoracle", description="Ask about cryptocurrency or any other question")
async def dearoracle(interaction: discord.Interaction, question: str):
    logger.info(f'Oracle question received from {interaction.user}: {question}')

    try:
        await interaction.response.defer()

        # Get crypto price if it's a crypto question
        if any(keyword in question.lower() for keyword in ['price', 'crypto', 'bitcoin', 'ethereum', 'btc', 'eth']):
            crypto_tools = CryptoTools()
            # Extract crypto name from question
            crypto_names = ['bitcoin', 'btc', 'ethereum', 'eth']
            found_crypto = next((name for name in crypto_names if name in question.lower()), None)

            if found_crypto:
                price_info = await crypto_tools.get_crypto_price(found_crypto)
                response = deepseek_client.chat.completions.create(
                    model=AI_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a crypto-savvy oracle. Provide insights about the cryptocurrency along with its price. Keep your answer to a single focused paragraph of about 5-7 sentences. Always finish your thought; do not use numbered lists or multi-section breakdowns."},
                        {"role": "user", "content": f"Give me insights about {found_crypto}. Here's the current price info: {price_info}"}
                    ],
                    max_tokens=800,
                    temperature=0.7
                )
                oracle_response = response.choices[0].message.content.strip()
                await send_oracle_reply(interaction, oracle_response)
                return

        # For non-crypto questions
        response = deepseek_client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": STREET_ORACLE_SYSTEM},
                {"role": "user", "content": question}
            ],
            max_tokens=800,
            temperature=0.7
        )

        oracle_wisdom = response.choices[0].message.content.strip()
        await send_oracle_reply(interaction, oracle_wisdom)

    except Exception as e:
        logger.error(f'Error in dearoracle command: {str(e)}', exc_info=True)
        await interaction.followup.send(
            f"Yo {interaction.user.mention}, my crystal ball's acting up right now. Try again later! Error: {str(e)}"
        )


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

        # AI call
        response = deepseek_client.chat.completions.create(
            model=AI_MODEL,
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
                response = deepseek_client.chat.completions.create(
                    model=AI_MODEL,
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
        except Exception:
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
            response = deepseek_client.chat.completions.create(
                model=AI_MODEL,
                messages=[
                    {"role": "system", "content": """You are an expert content analyzer with deep understanding of American society.
                    Provide a comprehensive analysis with these sections:
                    1. Executive Summary (2-3 sentences)
                    2. Main Topics Covered (bullet points)
                    3. Key Arguments & Evidence
                    4. Notable Quotes or Statistics
                    5. Potential Counterarguments or Limitations
                    6. Practical Applications
                    7. How This Affects You:
                       - Personal Impact
                       - Community Impact
                       - Action Steps
                       Consider current factors like:
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
        response = deepseek_client.chat.completions.create(
            model=AI_MODEL,
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
        response = deepseek_client.chat.completions.create(
            model=AI_MODEL,
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


@bot.tree.command(name="listchannel", description="Find the most relevant channel(s) by meaning")
@discord.app_commands.describe(name="What you're looking for, e.g. Substack or Crypto")
async def listchannel(interaction: discord.Interaction, name: str):
    logger.info(f'listchannel search from {interaction.user} in {interaction.guild.name}: {name}')

    try:
        # Private "thinking" indicator; a no-match leaves no public trace.
        await interaction.response.defer(ephemeral=True)

        # Gather text-based channels the invoking user can actually view.
        candidates = []
        for ch in interaction.guild.channels:
            if not isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
                continue
            if not ch.permissions_for(interaction.user).view_channel:
                continue
            candidates.append({
                "id": ch.id,
                "name": ch.name,
                "topic": getattr(ch, "topic", None),
            })

        matches = find_channels(name, candidates, deepseek_client, AI_MODEL, limit=2)

        if not matches:
            await interaction.followup.send(
                f'Couldn\'t find a channel matching "{name}" 🤷\n'
                "Try a broader term, or it may not exist yet.",
                ephemeral=True,
            )
            return

        lines = [f'🔎 Top matches for "{name}":']
        for i, m in enumerate(matches, start=1):
            lines.append(f"{i}. <#{m['id']}> — {m['reason']}")

        # Public message so anyone in the channel can use the links.
        await interaction.channel.send("\n".join(lines))
        # Private confirmation closes out the ephemeral defer for the invoker.
        await interaction.followup.send("Posted the matches above 👆", ephemeral=True)

    except Exception as e:
        logger.error(f'Error in listchannel command: {str(e)}', exc_info=True)
        await interaction.followup.send(
            f"Ay {interaction.user.mention}, channel search glitched out. Try again! Error: {str(e)}",
            ephemeral=True,
        )


@bot.tree.command(name="exportmembers", description="Owner/admin only: export all members + join data as a CSV")
async def exportmembers(interaction: discord.Interaction):
    logger.info(f'exportmembers requested by {interaction.user} in {getattr(interaction.guild, "name", "DM")}')

    # Restricted: configured owner IDs or anyone with server Administrator.
    if not is_owner_or_admin(interaction):
        await interaction.response.send_message(
            "⛔ This command is restricted to the server owner/admins.", ephemeral=True
        )
        return
    if interaction.guild is None:
        await interaction.response.send_message(
            "Run this inside a server, not a DM.", ephemeral=True
        )
        return

    try:
        # Ephemeral: the export is private to the invoking admin.
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        # guild.members is only complete with the Server Members intent enabled.
        # If it looks short, pull the full roster from the gateway.
        members = guild.members
        if guild.member_count and len(members) < guild.member_count:
            try:
                members = [m async for m in guild.fetch_members(limit=None)]
            except Exception as e:
                logger.warning(f"fetch_members fell back to cache in {guild.name}: {e}")

        rows = []
        for m in members:
            rec = join_log.get(m.id) or {}
            rows.append({
                "user_id": str(m.id),
                "username": m.name,
                "global_name": m.global_name or "",
                "server_nick": m.nick or "",
                "tag": str(m),
                "is_bot": m.bot,
                "account_created_utc": m.created_at.isoformat() if m.created_at else "",
                "joined_at_utc": m.joined_at.isoformat() if m.joined_at else "",
                "premium_since_utc": m.premium_since.isoformat() if m.premium_since else "",
                "pending": m.pending,
                "roles": ";".join(r.name for r in m.roles if r.name != "@everyone"),
                "join_method": rec.get("method", "unknown"),
                "join_invite_code": rec.get("invite_code", ""),
                "inviter_tag": rec.get("inviter_tag", ""),
            })

        csv_text = build_member_csv(rows)
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = member_export_filename(guild.name, stamp)
        export_file = discord.File(io.BytesIO(csv_text.encode("utf-8")), filename=filename)

        tracked = sum(1 for r in rows if r["join_method"] != "unknown")
        await interaction.followup.send(
            f"📁 Exported **{len(rows)}** members from **{guild.name}**.\n"
            f"Join source known for {tracked} (the rest joined before tracking started).",
            file=export_file,
            ephemeral=True,
        )

    except Exception as e:
        logger.error(f'Error in exportmembers command: {str(e)}', exc_info=True)
        await interaction.followup.send(
            f"Export glitched out. Error: {str(e)}", ephemeral=True
        )


def _parse_iso(s):
    """Parse an ISO-8601 string (optional 'Z') to a datetime, or None."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


@bot.tree.command(name="growth", description="Owner/admin only: server growth & churn scoreboard")
async def growth(interaction: discord.Interaction):
    logger.info(f'growth requested by {interaction.user} in {getattr(interaction.guild, "name", "DM")}')

    if not is_owner_or_admin(interaction):
        await interaction.response.send_message(
            "⛔ This command is restricted to the server owner/admins.", ephemeral=True
        )
        return
    if interaction.guild is None:
        await interaction.response.send_message(
            "Run this inside a server, not a DM.", ephemeral=True
        )
        return

    try:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        members = guild.members
        if guild.member_count and len(members) < guild.member_count:
            try:
                members = [m async for m in guild.fetch_members(limit=None)]
            except Exception as e:
                logger.warning(f"fetch_members fell back to cache in {guild.name}: {e}")

        humans = [m for m in members if not m.bot]
        bots = len(members) - len(humans)
        now = datetime.now(timezone.utc)

        join_dates = [m.joined_at for m in humans if m.joined_at]
        current_ids = {m.id for m in humans}
        leave_events = leave_log.events()
        leave_dates = [_parse_iso(ev.get("left_at")) for ev in leave_events]

        windows = growth_windows(join_dates, leave_dates, now)
        cohorts = join_cohorts(join_dates, months=6)
        inviters = top_inviters(join_log.all_records(), current_ids, limit=8)
        leavers = recent_leavers(leave_events, now, days=30, limit=8)

        embed = discord.Embed(
            title=f"📈 Growth — {guild.name}",
            description=f"**{len(humans)}** humans · **{bots}** bots · **{len(members)}** total",
            color=0x2ecc71,
        )

        net_lines = []
        for days in (7, 30, 90):
            w = windows[days]
            net_lines.append(f"**{days}d:** +{w['joins']} / −{w['leaves']} = net {w['net']:+d}")
        embed.add_field(name="Net growth (joins − leaves)", value="\n".join(net_lines), inline=False)

        if cohorts:
            peak = max(c for _, c in cohorts) or 1
            trend = "\n".join(f"`{m}`  {'▰' * max(1, round(c / peak * 10))} {c}" for m, c in cohorts)
        else:
            trend = "No join data."
        embed.add_field(name="Join trend (per month)", value=trend, inline=False)

        if inviters:
            inv = "\n".join(f"**{tag}** — {n} kept" for tag, n in inviters)
        else:
            inv = "No attributed invites yet — tracking just started, so this fills in as people join."
        embed.add_field(name="Top inviters (recruits who stayed)", value=inv, inline=False)

        if leavers:
            lv = "\n".join(
                f"{r['username']} — left {r['left_at'].strftime('%Y-%m-%d')}"
                + (f" (after {r['tenure_days']}d)" if r['tenure_days'] is not None else "")
                for r in leavers
            )
        else:
            lv = "No departures recorded in the last 30 days."
        embed.add_field(name="Recent leavers (30d)", value=lv, inline=False)

        embed.set_footer(text="Invite + churn data accrues from when tracking started; pre-existing joins show as historical only.")

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f'Error in growth command: {str(e)}', exc_info=True)
        await interaction.followup.send(f"Scoreboard glitched out. Error: {str(e)}", ephemeral=True)


class SurveyModal(discord.ui.Modal):
    """The form members fill in — one text input per question (Discord caps at 5)."""

    def __init__(self, survey):
        super().__init__(title=(survey["topic"] or "Survey")[:45])
        self.survey_id = survey["id"]
        self._inputs = []
        for q in survey["questions"][:5]:
            field = discord.ui.TextInput(
                label=q[:45],
                style=discord.TextStyle.paragraph,
                required=False,
                max_length=500,
            )
            self.add_item(field)
            self._inputs.append(field)

    async def on_submit(self, interaction: discord.Interaction):
        survey_store.add_response(self.survey_id, {
            "user_id": str(interaction.user.id),
            "username": str(interaction.user),
            "answers": [f.value for f in self._inputs],
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        })
        await interaction.response.send_message(
            "✅ Thanks — your response was recorded.", ephemeral=True
        )
        await refresh_survey_message(self.survey_id)


async def refresh_survey_message(survey_id, closed=False):
    """Edit the posted survey message to show the current response count.

    When closed, the button view is removed; otherwise the view is left
    untouched (omitting `view` from edit() keeps the existing button).
    """
    survey = survey_store.get(survey_id)
    if not survey or not survey.get("message_id"):
        return
    channel = bot.get_channel(survey["channel_id"])
    if channel is None:
        try:
            channel = await bot.fetch_channel(survey["channel_id"])
        except Exception:
            return
    count = len(survey_store.responses(survey_id))
    content = survey_message_text(survey, count, closed=closed)
    try:
        msg = await channel.fetch_message(survey["message_id"])
        if closed:
            await msg.edit(content=content, view=None)
        else:
            await msg.edit(content=content)
    except Exception as e:
        logger.error(f"Failed to refresh survey message {survey_id}: {e}")


class SurveyTakeView(discord.ui.View):
    """Persistent view holding the 'Take the survey' button for one survey."""

    def __init__(self, survey_id):
        super().__init__(timeout=None)
        self.survey_id = survey_id
        button = discord.ui.Button(
            label="Take the survey",
            style=discord.ButtonStyle.primary,
            custom_id=f"survey_take:{survey_id}",
        )
        button.callback = self._on_take
        self.add_item(button)

    async def _on_take(self, interaction: discord.Interaction):
        survey = survey_store.get(self.survey_id)
        if not survey or not survey.get("active", True):
            await interaction.response.send_message("This survey is closed.", ephemeral=True)
            return
        await interaction.response.send_modal(SurveyModal(survey))


class SurveyPreviewView(discord.ui.View):
    """Ephemeral Post/Cancel shown to the admin before the survey goes live."""

    def __init__(self, author_id, topic, questions, channel):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.topic = topic
        self.questions = questions
        self.channel = channel

    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user.id == self.author_id

    @discord.ui.button(label="Post it", style=discord.ButtonStyle.success)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button):
        sid = uuid.uuid4().hex[:8]
        survey = {
            "id": sid,
            "topic": self.topic,
            "questions": self.questions,
            "channel_id": self.channel.id,
            "message_id": None,
            "created_by": self.author_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
        }
        survey_store.create(survey)
        view = SurveyTakeView(sid)
        bot.add_view(view)
        _registered_survey_views.add(sid)
        msg = await self.channel.send(survey_message_text(survey, 0), view=view)
        survey_store.set_message(sid, msg.id)
        await interaction.response.edit_message(
            content=f"✅ Posted to {self.channel.mention}. Survey ID `{sid}` — export anytime with `/surveyresults`.",
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled — nothing posted.", view=None)


@bot.tree.command(name="survey", description="Owner/admin only: create an AI-generated survey")
@discord.app_commands.describe(
    topic="What you want to learn — the AI writes the questions",
    count="How many questions (1-5, default 4)",
    questions="Optional: your own questions separated by | (overrides the AI)",
)
async def survey(interaction: discord.Interaction, topic: str = None, count: int = 4, questions: str = None):
    logger.info(f'survey requested by {interaction.user}: topic={topic!r} count={count}')

    if not is_owner_or_admin(interaction):
        await interaction.response.send_message(
            "⛔ This command is restricted to the server owner/admins.", ephemeral=True
        )
        return
    if interaction.guild is None:
        await interaction.response.send_message("Run this inside a server, not a DM.", ephemeral=True)
        return

    count = max(1, min(5, count))
    try:
        await interaction.response.defer(ephemeral=True)

        if questions:
            qs = [q.strip()[:45] for q in questions.split("|") if q.strip()][:5]
        elif topic:
            qs = generate_questions(topic, count, deepseek_client, AI_MODEL)
        else:
            await interaction.followup.send(
                "Give me a `topic` (the AI writes the questions) or your own `questions` separated by `|`.",
                ephemeral=True,
            )
            return

        if not qs:
            await interaction.followup.send(
                "Couldn't produce any questions — try rephrasing the topic.", ephemeral=True
            )
            return

        topic_label = topic or "Survey"
        preview = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(qs))
        view = SurveyPreviewView(interaction.user.id, topic_label, qs, interaction.channel)
        await interaction.followup.send(
            f"**Preview — {topic_label}**\n{preview}\n\nPost this survey to {interaction.channel.mention}?",
            view=view,
            ephemeral=True,
        )

    except Exception as e:
        logger.error(f'Error in survey command: {str(e)}', exc_info=True)
        await interaction.followup.send(f"Survey setup glitched out. Error: {str(e)}", ephemeral=True)


@bot.tree.command(name="surveyresults", description="Owner/admin only: export survey responses as CSV")
@discord.app_commands.describe(survey_id="Survey ID (leave blank for the most recent survey)")
async def surveyresults(interaction: discord.Interaction, survey_id: str = None):
    logger.info(f'surveyresults requested by {interaction.user}: id={survey_id}')

    if not is_owner_or_admin(interaction):
        await interaction.response.send_message(
            "⛔ This command is restricted to the server owner/admins.", ephemeral=True
        )
        return

    try:
        await interaction.response.defer(ephemeral=True)
        survey = survey_store.get(survey_id) if survey_id else survey_store.latest()
        if not survey:
            await interaction.followup.send(
                "No survey found. Create one with `/survey` first.", ephemeral=True
            )
            return

        responses = survey_store.responses(survey["id"])
        csv_text = build_survey_csv(survey, responses)
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        export_file = discord.File(
            io.BytesIO(csv_text.encode("utf-8")), filename=f"survey_{survey['id']}_{stamp}.csv"
        )
        await interaction.followup.send(
            f"📁 **{len(responses)}** responses for **{survey['topic']}** (ID `{survey['id']}`).",
            file=export_file,
            ephemeral=True,
        )

    except Exception as e:
        logger.error(f'Error in surveyresults command: {str(e)}', exc_info=True)
        await interaction.followup.send(f"Export glitched out. Error: {str(e)}", ephemeral=True)


@bot.tree.command(name="closesurvey", description="Owner/admin only: stop a survey from accepting responses")
@discord.app_commands.describe(survey_id="Survey ID (leave blank for the most recent survey)")
async def closesurvey(interaction: discord.Interaction, survey_id: str = None):
    logger.info(f'closesurvey requested by {interaction.user}: id={survey_id}')

    if not is_owner_or_admin(interaction):
        await interaction.response.send_message(
            "⛔ This command is restricted to the server owner/admins.", ephemeral=True
        )
        return

    try:
        await interaction.response.defer(ephemeral=True)
        survey = survey_store.get(survey_id) if survey_id else survey_store.latest()
        if not survey:
            await interaction.followup.send("No survey found.", ephemeral=True)
            return
        if not survey.get("active", True):
            await interaction.followup.send(
                f"Survey `{survey['id']}` is already closed.", ephemeral=True
            )
            return

        survey_store.close(survey["id"])
        _registered_survey_views.discard(survey["id"])
        await refresh_survey_message(survey["id"], closed=True)

        count = len(survey_store.responses(survey["id"]))
        await interaction.followup.send(
            f"🔒 Closed **{survey['topic']}** (`{survey['id']}`). {count} responses collected — "
            f"export with `/surveyresults survey_id:{survey['id']}`.",
            ephemeral=True,
        )

    except Exception as e:
        logger.error(f'Error in closesurvey command: {str(e)}', exc_info=True)
        await interaction.followup.send(f"Close glitched out. Error: {str(e)}", ephemeral=True)


@bot.event
async def on_command_error(ctx, error):
    logger.error(f'Command error: {str(error)}', exc_info=True)
    await ctx.send(f"An error occurred: {str(error)}")


async def handle_oracle_mention(message):
    """Answer an @mention in the Street Oracle voice, with short conversation memory."""
    # Strip the bot mention(s) to recover the actual question.
    question = message.content
    for mention in (f"<@{bot.user.id}>", f"<@!{bot.user.id}>"):
        question = question.replace(mention, "")
    question = question.strip()

    if not question:
        await message.reply(
            "Young God, you summoned me but spoke no question. Ask, and I will answer."
        )
        return

    key = make_key(message.channel.id, message.author.id)
    now = time.time()

    try:
        async with message.channel.typing():
            oracle_memory.prune(now)
            history = oracle_memory.get_history(key, now)
            messages = build_messages(STREET_ORACLE_SYSTEM, history, question)
            response = deepseek_client.chat.completions.create(
                model=AI_MODEL,
                messages=messages,
                max_tokens=800,
                temperature=0.7,
            )
            answer = response.choices[0].message.content.strip()

        chunks = split_for_discord(answer)
        await message.reply(chunks[0])
        for chunk in chunks[1:]:
            await message.channel.send(chunk)

        # Record both turns only after a successful send.
        oracle_memory.append_turn(key, "user", question, now)
        oracle_memory.append_turn(key, "assistant", answer, now)

    except Exception as e:
        logger.error(f'Error in handle_oracle_mention: {str(e)}', exc_info=True)
        await message.reply(
            "Young God, my vision clouds for the moment. Ask me again shortly."
        )


@bot.event
async def on_message(message):
    # Never respond to ourselves or any other bot (prevents reply loops).
    if message.author.bot:
        return

    # Existing prefix-command path stays first and unchanged.
    if message.content.startswith(bot.command_prefix):
        try:
            ctx = await bot.get_context(message)
            if ctx.valid:
                await bot.invoke(ctx)
        except Exception as e:
            logger.error(f"Error in on_message (command path): {str(e)}", exc_info=True)
        return  # a prefix command is not also a mention prompt

    # @mention path: only a genuine direct mention of the bot.
    if bot.user is None or not bot.user.mentioned_in(message):
        return
    if message.mention_everyone or bot.user not in message.mentions:
        return  # ignore @everyone/@here and role-only pings

    await handle_oracle_mention(message)


# Main entry point
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run_bot_and_server())
    except KeyboardInterrupt:
        loop.run_until_complete(bot.close())
    finally:
        loop.close()
