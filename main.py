import discord
from discord.ui import Modal, TextInput, Button, View
from discord import app_commands
import gspread
from google.oauth2 import service_account
import asyncio
from typing import Optional
from datetime import datetime
import re
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import io
import os
import json

# Configuration
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', 'YOUR_BOT_TOKEN_HERE')
CHANNEL_ID = int(os.environ.get('CHANNEL_ID', '1420084335020740658'))
SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID', '1ZdZVIwUkBeEA6VFhS5lfZQbH9oZ1KHBeOIZPjbDDJoI')
ROLE_ID = int(os.environ.get('ROLE_ID', '935863068636897300'))
AUTHORIZED_ROLE_ID = int(os.environ.get('AUTHORIZED_ROLE_ID', '123456789012345678'))
DESTINATION_CHANNEL_ID = int(os.environ.get('DESTINATION_CHANNEL_ID', '1369427073567031436'))

GOOGLE_SHEETS_CREDENTIALS = 'credentials.json'

# Create placeholder credentials if file doesn't exist
if not os.path.exists(GOOGLE_SHEETS_CREDENTIALS):
    print("⚠️ credentials.json not found - creating placeholder")
    with open(GOOGLE_SHEETS_CREDENTIALS, 'w') as f:
        json.dump({
            "type": "service_account",
            "project_id": "placeholder",
            "private_key_id": "placeholder",
            "private_key": "placeholder",
            "client_email": "placeholder@placeholder.iam.gserviceaccount.com",
            "client_id": "placeholder",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs"
        }, f)

intents = discord.Intents.all()
intents.message_content = True

class MyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print(f'✅ Bot ready with name: {self.user}')

bot = MyBot()

def get_google_sheets_client():
    """Initialize and return Google Sheets client"""
    try:
        creds = service_account.Credentials.from_service_account_file(
            GOOGLE_SHEETS_CREDENTIALS,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        print(f"Google Sheets connection error: {e}")
        return None

def get_status_color(status: str) -> discord.Color:
    """Return Discord color based on bet status"""
    color_map = {
        "Open": discord.Color.blue(),
        "Won": discord.Color.green(),
        "Lost": discord.Color.red(),
        "Draw": discord.Color.gold()
    }
    return color_map.get(status, discord.Color.blue())

async def has_button_permission(interaction: discord.Interaction, sheet_row_number: int) -> bool:
    """Check if user has permission to modify a bet"""
    try:
        client = get_google_sheets_client()
        if not client:
            return False

        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        worksheet = spreadsheet.get_worksheet(0)
        creator_id = worksheet.cell(sheet_row_number, 1).value

        # Permission granted to: creator, authorized role, or admins
        if str(interaction.user.id) == creator_id:
            return True

        if any(role.id == AUTHORIZED_ROLE_ID for role in interaction.user.roles):
            return True

        if interaction.user.guild_permissions.administrator:
            return True

        return False

    except Exception as e:
        print(f"ERROR in permission check: {e}")
        return False

async def copy_bet_message(original_message: discord.Message, sheet_row_number: int):
    """Copy bet message to destination channel and store message ID"""
    try:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
        if not destination_channel:
            print(f"❌ Destination channel {DESTINATION_CHANNEL_ID} not found")
            return None

        if not original_message.embeds:
            print("❌ No embeds found in original message")
            return None

        original_embed = original_message.embeds[0]
        bet_description = original_embed.title.replace("🎯 ", "") if original_embed.title else "Unknown Bet"

        # Extract bet details from embed fields
        odds = "?"
        units = "?"
        betslip = ""
        versus = ""

        for field in original_embed.fields:
            if "Odds:" in field.name and "Units:" in field.name:
                odds_match = re.search(r"Odds:\s*\*\*([\d.]+)\*\*", field.name)
                units_match = re.search(r"Units:\s*\*\*([\d.]+)u\*\*", field.name)

                if odds_match:
                    odds = odds_match.group(1)
                if units_match:
                    units = units_match.group(1)

            if field.name == "📋 Betslip":
                betslip = field.value

            if field.name == "⚔️ Versus":
                versus = field.value

        # Format message with versus if available
        if versus and versus.strip():
            formatted_message = f"<@&{ROLE_ID}>\n{bet_description} @{odds} vs {versus} - {units}u"
        else:
            formatted_message = f"<@&{ROLE_ID}>\n{bet_description} @{odds} - {units}u"

        if betslip:
            formatted_message += f"\n{betslip}"

        copy_message = await destination_channel.send(formatted_message)

        # Add dollar emoji reaction
        try:
            await copy_message.add_reaction('<:takemymoney:1255883106553172090>')
        except Exception as e:
            print(f"⚠️ Could not add dollar emoji reaction: {e}")

        # Store copy message ID in Google Sheets
        try:
            client = get_google_sheets_client()
            if client:
                spreadsheet = client.open_by_key(SPREADSHEET_ID)
                worksheet = spreadsheet.get_worksheet(0)
                worksheet.update_cell(sheet_row_number, 11, str(copy_message.id))
                print(f"✅ Stored copy message ID {copy_message.id} for row {sheet_row_number}")
        except Exception as e:
            print(f"❌ Error storing copy message ID: {e}")

        return copy_message.id

    except Exception as e:
        print(f"❌ Error copying bet message: {e}")
        return None

async def update_copied_message(sheet_row_number: int, original_message: discord.Message = None):
    """Update the copied message when bet status changes"""
    try:
        client = get_google_sheets_client()
        if not client:
            return False

        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        worksheet = spreadsheet.get_worksheet(0)

        copy_message_id = worksheet.cell(sheet_row_number, 11).value
        if not copy_message_id:
            print(f"❌ No copy message ID found for row {sheet_row_number}")
            return False

        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
        if not destination_channel:
            print(f"❌ Destination channel {DESTINATION_CHANNEL_ID} not found")
            return False

        # Fetch original message if not provided
        if not original_message:
            original_message_id = worksheet.cell(sheet_row_number, 8).value
            if not original_message_id or original_message_id == "PENDING":
                return False

            channel = bot.get_channel(CHANNEL_ID)
            if not channel:
                return False

            try:
                original_message = await channel.fetch_message(int(original_message_id))
            except:
                return False

        try:
            copy_message = await destination_channel.fetch_message(int(copy_message_id))
        except discord.NotFound:
            print(f"❌ Copy message {copy_message_id} not found, creating new copy")
            new_copy_id = await copy_bet_message(original_message, sheet_row_number)
            return new_copy_id is not None
        except Exception as e:
            print(f"❌ Error fetching copy message: {e}")
            return False

        # Extract data from Google Sheets
        try:
            row_data = worksheet.row_values(sheet_row_number)
            bet_description = row_data[3] if len(row_data) > 3 else "Unknown Bet"
            odds = row_data[4] if len(row_data) > 4 else "?"
            units = row_data[5] if len(row_data) > 5 else "?"
            status = row_data[2] if len(row_data) > 2 else "Open"
            betslip = row_data[6] if len(row_data) > 6 and row_data[6] and row_data[6] != "None" else ""
            versus = row_data[11] if len(row_data) > 11 and row_data[11] and row_data[11] != "None" else ""

        except Exception as e:
            print(f"❌ Error reading from Google Sheets: {e}")
            # Fallback to reading from original message embed
            if original_message.embeds:
                original_embed = original_message.embeds[0]
                bet_description = original_embed.title.replace("🎯 ", "") if original_embed.title else "Unknown Bet"
                odds = "?"
                units = "?"
                betslip = ""
                versus = ""

                status = "Open"
                if original_embed.description and "Status:" in original_embed.description:
                    status_match = re.search(r"Status:\s*\*\*(\w+)\*\*", original_embed.description)
                    if status_match:
                        status = status_match.group(1)
                    else:
                        if "Won" in original_embed.description:
                            status = "Won"
                        elif "Lost" in original_embed.description:
                            status = "Lost"
                        elif "Draw" in original_embed.description:
                            status = "Draw"

                for field in original_embed.fields:
                    if "Odds:" in field.name and "Units:" in field.name:
                        odds_match = re.search(r"Odds:\s*\*\*([\d.]+)\*\*", field.name)
                        units_match = re.search(r"Units:\s*\*\*([\d.]+)u\*\*", field.name)

                        if odds_match:
                            odds = odds_match.group(1)
                        if units_match:
                            units = units_match.group(1)

                    if field.name == "📋 Betslip":
                        betslip = field.value

                    if field.name == "⚔️ Versus":
                        versus = field.value
            else:
                bet_description = "Unknown Bet"
                odds = "?"
                units = "?"
                status = "Open"
                betslip = ""
                versus = ""

        # Create updated message
        if versus and versus.strip():
            formatted_message = f"<@&{ROLE_ID}>\n{bet_description} @{odds} vs {versus} - {units}u"
        else:
            formatted_message = f"<@&{ROLE_ID}>\n{bet_description} @{odds} - {units}u"

        if betslip:
            formatted_message += f"\n{betslip}"

        await copy_message.edit(content=formatted_message)

        # Update reactions based on status
        try:
            status_emoji_map = {
                "Won": "✅",
                "Lost": "❌", 
                "Draw": "🔄"
            }
            
            current_reactions = [str(reaction.emoji) for reaction in copy_message.reactions]
            
            print(f"🔍 Current reactions: {current_reactions}, Target status: {status}")
            
            # Remove all existing status emojis
            for status_emoji in status_emoji_map.values():
                if status_emoji in current_reactions:
                    await copy_message.clear_reaction(status_emoji)
                    print(f"🗑️ Removed {status_emoji} reaction")
                    current_reactions = [str(reaction.emoji) for reaction in copy_message.reactions]
            
            # For Open status: only keep dollar emoji
            if status == "Open":
                if "<:takemymoney:1255883106553172090>" not in current_reactions:
                    await copy_message.add_reaction('<:takemymoney:1255883106553172090>')
                    print("<:takemymoney:1255883106553172090> Added dollar emoji for Open status")
                print(f"✅ Reset reactions for Open status - only <:takemymoney:1255883106553172090> remains")
            else:
                # For settled statuses: add both dollar and status emoji
                if status in status_emoji_map:
                    if "<:takemymoney:1255883106553172090>" not in current_reactions:
                        await copy_message.add_reaction('<:takemymoney:1255883106553172090>')
                        print("<:takemymoney:1255883106553172090> Added dollar emoji")
                    
                    await copy_message.add_reaction(status_emoji_map[status])
                    print(f"✅ Added {status_emoji_map[status]} reaction for {status} status")
                    
        except Exception as e:
            print(f"⚠️ Error updating reactions: {e}")

        print(f"✅ Updated copy message for row {sheet_row_number} with status: {status}")
        return True

    except Exception as e:
        print(f"❌ Error updating copied message: {e}")
        return False

async def complete_bet(interaction: discord.Interaction, final_status: str, sheet_row_number: int):
    """Mark bet as completed (Won/Lost/Draw) and calculate profit"""
    try:
        if not await has_button_permission(interaction, sheet_row_number):
            await interaction.followup.send("❌ You don't have permission to modify this bet!", ephemeral=True)
            return

        client = get_google_sheets_client()
        if not client:
            await interaction.followup.send("❌ Google Sheets connection error!", ephemeral=True)
            return

        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        worksheet = spreadsheet.get_worksheet(0)

        message_id = worksheet.cell(sheet_row_number, 8).value
        if not message_id or message_id == "PENDING":
            await interaction.followup.send("❌ Error: Could not find message ID for this bet.", ephemeral=True)
            return

        row_data = worksheet.row_values(sheet_row_number)

        # Extract odds and units
        try:
            if len(row_data) > 4:
                odds = float(row_data[4]) if row_data[4] else 1.0
            else:
                odds = 1.0
        except (ValueError, TypeError):
            odds = 1.0

        try:
            if len(row_data) > 5:
                units = float(row_data[5]) if row_data[5] else 1.0
            else:
                units = 1.0
        except (ValueError, TypeError):
            units = 1.0

        # Calculate profit based on status
        if final_status == "Won":
            profit = units * odds - units
            result_text = f"**+{profit:.2f}u**"
        elif final_status == "Lost":
            profit = -units
            result_text = f"{profit:.2f}u"
        else:
            profit = 0
            result_text = "0 U"

        # Update Google Sheets
        worksheet.update_cell(sheet_row_number, 3, final_status)
        worksheet.update_cell(sheet_row_number, 10, profit)

        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            await interaction.followup.send("❌ Error: Could not find channel.", ephemeral=True)
            return

        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            await interaction.followup.send("❌ Error: Could not find bet message.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send("❌ Error: No permission to access message.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Error fetching message: {e}", ephemeral=True)
            return

        if not message.embeds:
            await interaction.followup.send("❌ Error: Could not find bet information in the message.", ephemeral=True)
            return

        # Update message embed
        embed = message.embeds[0]
        embed.description = f"**Status:** {final_status}"
        embed.color = get_status_color(final_status)

        field_names = [field.name for field in embed.fields]

        # Remove potential payout field
        payout_field_index = next((i for i, field in enumerate(embed.fields) if field.name == "💰 Potential Payout"), None)
        if payout_field_index is not None:
            embed.remove_field(payout_field_index)
            field_names = [field.name for field in embed.fields]

        # Add/update profit field
        profit_field_index = next((i for i, field in enumerate(embed.fields) if field.name == "💰 Profit"), None)

        if profit_field_index is not None:
            embed.set_field_at(profit_field_index, name="💰 Profit", value=result_text, inline=True)
        else:
            details_field_index = next((i for i, field in enumerate(embed.fields) if field.name == "📊 Details"), None)
            if details_field_index is not None:
                embed.insert_field_at(details_field_index + 1, name="💰 Profit", value=result_text, inline=True)
            else:
                embed.add_field(name="💰 Profit", value=result_text, inline=True)

        # Replace buttons with unlock button
        view = View(timeout=None)
        view.add_item(UnlockButton(sheet_row_number))

        await message.edit(embed=embed, view=view)

        # Update copied message
        try:
            success = await update_copied_message(sheet_row_number, message)
            if success:
                print(f"✅ Successfully updated copied message for row {sheet_row_number}")
            else:
                print(f"❌ Failed to update copied message for row {sheet_row_number}")
        except Exception as e:
            print(f"❌ Error updating copied message: {e}")

        await interaction.followup.send(f"✅ Bet marked as **{final_status}**! Profit recorded: {profit:.2f} units", ephemeral=True)

    except Exception as e:
        print(f"ERROR in complete_bet: {e}")
        await interaction.followup.send(f"❌ Error completing bet: {str(e)}", ephemeral=True)

class UnlockButton(Button):
    """Button to unlock a completed bet for editing"""
    def __init__(self, sheet_row_number: int):
        super().__init__(
            label="🔓 Unlock",
            style=discord.ButtonStyle.secondary,
            row=0,
            custom_id=f"unlock_{sheet_row_number}"
        )
        self.sheet_row_number = sheet_row_number

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True, ephemeral=True)

            if not await has_button_permission(interaction, self.sheet_row_number):
                await interaction.followup.send("❌ You don't have permission to modify this bet!", ephemeral=True)
                return

            client = get_google_sheets_client()
            if not client:
                await interaction.followup.send("❌ Google Sheets connection error!", ephemeral=True)
                return

            spreadsheet = client.open_by_key(SPREADSHEET_ID)
            worksheet = spreadsheet.get_worksheet(0)

            # Reset bet status to Open
            worksheet.update_cell(self.sheet_row_number, 3, "Open")
            worksheet.update_cell(self.sheet_row_number, 10, "")

            message_id = worksheet.cell(self.sheet_row_number, 8).value

            channel = bot.get_channel(CHANNEL_ID)
            if not channel:
                await interaction.followup.send("❌ Error: Could not find channel.", ephemeral=True)
                return

            message = await channel.fetch_message(int(message_id))

            if not message.embeds:
                await interaction.followup.send("❌ Error: Could not find bet information in the message.", ephemeral=True)
                return

            # Update message embed
            embed = message.embeds[0]
            embed.description = "**Status:** Open"
            embed.color = get_status_color("Open")

            field_names = [field.name for field in embed.fields]

            # Remove profit field
            profit_field_index = next((i for i, field in enumerate(embed.fields) if field.name == "💰 Profit"), None)
            if profit_field_index is not None:
                embed.remove_field(profit_field_index)

            # Add potential payout field
            row_data = worksheet.row_values(self.sheet_row_number)
            try:
                odds = float(row_data[4]) if len(row_data) > 4 and row_data[4] else 1.0
                units = float(row_data[5]) if len(row_data) > 5 and row_data[5] else 1.0
                potential_payout = units * odds

                details_field_index = next((i for i, field in enumerate(embed.fields) if field.name == "📊 Details"), None)
                if details_field_index is not None:
                    embed.insert_field_at(details_field_index + 1, name="💰 Potential Payout", value=f"**{potential_payout:.2f}u**", inline=True)
                else:
                    embed.add_field(name="💰 Potential Payout", value=f"**{potential_payout:.2f}u**", inline=True)
            except (ValueError, TypeError):
                pass

            # Restore action buttons
            view = View(timeout=None)
            view.add_item(WonButton(self.sheet_row_number))
            view.add_item(LostButton(self.sheet_row_number))
            view.add_item(DrawButton(self.sheet_row_number))
            view.add_item(EditButton(self.sheet_row_number))

            await message.edit(embed=embed, view=view)

            await update_copied_message(self.sheet_row_number, message)

            await interaction.followup.send("✅ Bet unlocked! Action buttons restored.", ephemeral=True)

        except Exception as e:
            print(f"ERROR in UnlockButton: {e}")
            await interaction.followup.send(f"❌ Error unlocking bet: {str(e)}", ephemeral=True)

class WonButton(Button):
    """Button to mark bet as Won"""
    def __init__(self, sheet_row_number: int):
        super().__init__(
            label="✅ Won",
            style=discord.ButtonStyle.success,
            row=0,
            custom_id=f"won_{sheet_row_number}"
        )
        self.sheet_row_number = sheet_row_number

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True, ephemeral=True)

            if not await has_button_permission(interaction, self.sheet_row_number):
                await interaction.followup.send("❌ You don't have permission to modify this bet!", ephemeral=True)
                return

            await complete_bet(interaction, "Won", self.sheet_row_number)
        except Exception as e:
            print(f"Error in WonButton: {e}")
            await interaction.followup.send("❌ Error processing request. Please try again.", ephemeral=True)

class LostButton(Button):
    """Button to mark bet as Lost"""
    def __init__(self, sheet_row_number: int):
        super().__init__(
            label="❌ Lost",
            style=discord.ButtonStyle.danger,
            row=0,
            custom_id=f"lost_{sheet_row_number}"
        )
        self.sheet_row_number = sheet_row_number

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True, ephemeral=True)

            if not await has_button_permission(interaction, self.sheet_row_number):
                await interaction.followup.send("❌ You don't have permission to modify this bet!", ephemeral=True)
                return

            await complete_bet(interaction, "Lost", self.sheet_row_number)
        except Exception as e:
            print(f"Error in LostButton: {e}")
            await interaction.followup.send("❌ Error processing request. Please try again.", ephemeral=True)

class DrawButton(Button):
    """Button to mark bet as Draw"""
    def __init__(self, sheet_row_number: int):
        super().__init__(
            label="🤝 Draw",
            style=discord.ButtonStyle.secondary,
            row=0,
            custom_id=f"draw_{sheet_row_number}"
        )
        self.sheet_row_number = sheet_row_number

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True, ephemeral=True)

            if not await has_button_permission(interaction, self.sheet_row_number):
                await interaction.followup.send("❌ You don't have permission to modify this bet!", ephemeral=True)
                return

            await complete_bet(interaction, "Draw", self.sheet_row_number)
        except Exception as e:
            print(f"Error in DrawButton: {e}")
            await interaction.followup.send("❌ Error processing request. Please try again.", ephemeral=True)

class EditButton(Button):
    """Button to open edit modal for a bet"""
    def __init__(self, sheet_row_number: int):
        super().__init__(
            label="✏️ Edit",
            style=discord.ButtonStyle.primary,
            row=0,
            custom_id=f"edit_{sheet_row_number}"
        )
        self.sheet_row_number = sheet_row_number

    async def callback(self, interaction: discord.Interaction):
        try:
            if not await has_button_permission(interaction, self.sheet_row_number):
                await interaction.followup.send(
                    "❌ You don't have permission to modify this bet!",
                    ephemeral=True
                )
                return

            modal = EditBetModal(self.sheet_row_number)
            await interaction.response.send_modal(modal)

        except Exception as e:
            print(f"Error in EditButton: {e}")
            await interaction.followup.send(
                "❌ Error opening edit form. Please try again.",
                ephemeral=True
            )

class BetModal(Modal, title='Create New Bet'):
    """Modal for creating a new bet"""
    def __init__(self):
        super().__init__()

        self.bet_input = TextInput(
            label='Bet',
            placeholder='Enter your bet description...',
            max_length=200,
            required=True
        )

        self.versus_input = TextInput(
            label='Versus (Optional)',
            placeholder='Enter versus information...',
            max_length=100,
            required=False
        )

        self.odds_input = TextInput(
            label='Odds',
            placeholder='Enter odds (e.g., 2.36)...',
            default='1.95',
            max_length=10,
            required=True
        )

        self.units_input = TextInput(
            label='Units',
            placeholder='Enter units (e.g., 4)...',
            default='1',
            max_length=10,
            required=True
        )

        self.betslip_input = TextInput(
            label='Betslip (Optional)',
            placeholder='Enter betslip information...',
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=False
        )

        self.add_item(self.bet_input)
        self.add_item(self.versus_input)
        self.add_item(self.odds_input)
        self.add_item(self.units_input)
        self.add_item(self.betslip_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        status = "Open"

        # Validate odds and units
        try:
            odds = float(self.odds_input.value)
            units = float(self.units_input.value)
            if odds <= 0 or units <= 0:
                raise ValueError("Values must be positive")
        except ValueError:
            await interaction.followup.send("❌ Odds and Units must be valid positive numbers!", ephemeral=True)
            return

        try:
            client = get_google_sheets_client()
            if not client:
                await interaction.followup.send("❌ Google Sheets connection error!", ephemeral=True)
                return

            spreadsheet = client.open_by_key(SPREADSHEET_ID)
            worksheet = spreadsheet.get_worksheet(0)

            creation_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Prepare row data for Google Sheets
            row_data = [
                str(interaction.user.id),
                interaction.user.name,
                status,
                self.bet_input.value,
                float(self.odds_input.value),
                float(self.units_input.value),
                self.betslip_input.value if self.betslip_input.value else "",
                "PENDING",
                creation_timestamp,
                "",
                "",
                self.versus_input.value if self.versus_input.value else ""
            ]

            worksheet.append_row(row_data)

            all_values = worksheet.get_all_values()
            sheet_row_number = len(all_values)

            potential_payout = float(self.units_input.value) * float(self.odds_input.value)

            # Create bet embed
            embed = discord.Embed(
                title=f"🎯 {self.bet_input.value}",
                description=f"Status: **{status}**",
                color=get_status_color(status),
                timestamp=datetime.now()
            )

            # Add versus field if provided
            if self.versus_input.value and self.versus_input.value.strip():
                embed.add_field(
                    name="⚔️ Versus",
                    value=self.versus_input.value,
                    inline=False
                )

            embed.add_field(
                name= f":coin: Odds: **{self.odds_input.value}** - Units: **{self.units_input.value}u**",
                value=f"<@&{ROLE_ID}>",
                inline=True
            )

            embed.add_field(
                name="💰 Potential Payout",
                value=f"**{potential_payout:.2f}u**",
                inline=True
            )

            if self.betslip_input.value and self.betslip_input.value.strip():
                embed.add_field(
                    name="📋 Betslip",
                    value=self.betslip_input.value,
                    inline=False
                )

            # Add action buttons
            view = View(timeout=None)
            view.add_item(WonButton(sheet_row_number))
            view.add_item(LostButton(sheet_row_number))
            view.add_item(DrawButton(sheet_row_number))
            view.add_item(EditButton(sheet_row_number))

            channel = bot.get_channel(CHANNEL_ID)
            if channel:
                message = await channel.send(embed=embed, view=view)

                worksheet.update_cell(sheet_row_number, 8, str(message.id))

                # Copy message to destination channel
                copy_message_id = await copy_bet_message(message, sheet_row_number)
                if copy_message_id:
                    print(f"✅ Bet message copied to destination channel with ID: {copy_message_id}")
                else:
                    print("❌ Failed to copy bet message to destination channel")

                await interaction.followup.send(f"✅ Bet created successfully at row {sheet_row_number}!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Channel not found!", ephemeral=True)

        except Exception as e:
            print(f"ERROR in BetModal: {e}")
            await interaction.followup.send(f"❌ Error saving data: {str(e)}", ephemeral=True)

class EditBetModal(Modal, title='Edit Bet Details'):
    """Modal for editing existing bet details"""
    def __init__(self, sheet_row_number: int):
        super().__init__()
        self.sheet_row_number = sheet_row_number

        try:
            client = get_google_sheets_client()
            if not client:
                print("ERROR: Google Sheets client not available")
                return

            spreadsheet = client.open_by_key(SPREADSHEET_ID)
            worksheet = spreadsheet.get_worksheet(0)

            all_values = worksheet.get_all_values()
            if self.sheet_row_number > len(all_values):
                raise IndexError(f"Row {self.sheet_row_number} doesn't exist")

            row_data = worksheet.row_values(self.sheet_row_number)

            betslip_value = ""
            if len(row_data) > 6:
                betslip_value = row_data[6]
                if betslip_value == "None":
                    betslip_value = ""

            versus_value = ""
            if len(row_data) > 11:
                versus_value = row_data[11]
                if versus_value == "None":
                    versus_value = ""

            # Pre-fill form with existing data
            self.bet_input = TextInput(
                label='Bet*',
                default=row_data[3] if len(row_data) > 3 else "",
                max_length=200,
                required=True
            )

            self.versus_input = TextInput(
                label='Versus (Optional)',
                default=versus_value,
                max_length=100,
                required=False
            )

            self.odds_input = TextInput(
                label='Odds*',
                default=row_data[4] if len(row_data) > 4 else "1.0",
                max_length=10,
                required=True
            )

            self.units_input = TextInput(
                label='Units*',
                default=row_data[5] if len(row_data) > 5 else "1.0",
                max_length=10,
                required=True
            )

            self.betslip_input = TextInput(
                label='Betslip (Optional)',
                default=betslip_value,
                style=discord.TextStyle.paragraph,
                max_length=500,
                required=False
            )

            print("DEBUG: Edit form created successfully with existing data")

        except Exception as e:
            print(f"ERROR loading data for edit: {e}")
            self.bet_input = TextInput(label='Bet*', placeholder='Enter bet description...', max_length=200, required=True)
            self.versus_input = TextInput(label='Versus (Optional)', placeholder='Enter versus information...', max_length=100, required=False)
            self.odds_input = TextInput(label='Odds*', default='1.0', max_length=10, required=True)
            self.units_input = TextInput(label='Units*', default='1.0', max_length=10, required=True)
            self.betslip_input = TextInput(label='Betslip', placeholder='Enter betslip info...', style=discord.TextStyle.paragraph, max_length=500, required=False)

        self.add_item(self.bet_input)
        self.add_item(self.versus_input)
        self.add_item(self.odds_input)
        self.add_item(self.units_input)
        self.add_item(self.betslip_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        # Validate inputs
        try:
            odds = float(self.odds_input.value)
            units = float(self.units_input.value)
            if odds <= 0 or units <= 0:
                raise ValueError("Values must be positive")
        except ValueError:
            await interaction.followup.send("❌ Odds and Units must be valid positive numbers!", ephemeral=True)
            return

        try:
            client = get_google_sheets_client()
            if not client:
                await interaction.followup.send("❌ Google Sheets connection error!", ephemeral=True)
                return

            spreadsheet = client.open_by_key(SPREADSHEET_ID)
            worksheet = spreadsheet.get_worksheet(0)

            # Update Google Sheets with new values
            updates = [
                (self.sheet_row_number, 4, self.bet_input.value),
                (self.sheet_row_number, 5, str(float(self.odds_input.value))),
                (self.sheet_row_number, 6, str(float(self.units_input.value))),
                (self.sheet_row_number, 7, self.betslip_input.value if self.betslip_input.value else ""),
                (self.sheet_row_number, 12, self.versus_input.value if self.versus_input.value else "")
            ]

            for row, col, value in updates:
                worksheet.update_cell(row, col, value)

            message_id = worksheet.cell(self.sheet_row_number, 8).value
            if not message_id or message_id == "PENDING":
                await interaction.followup.send("❌ Error: Could not find message ID for this bet.", ephemeral=True)
                return

            channel = bot.get_channel(CHANNEL_ID)
            if not channel:
                await interaction.followup.send("❌ Error: Could not find channel.", ephemeral=True)
                return

            message = await channel.fetch_message(int(message_id))

            if not message.embeds:
                await interaction.followup.send("❌ Error: Could not find bet information in the message.", ephemeral=True)
                return

            embed = message.embeds[0]

            # Update embed with new values
            embed.title = f"🎯 {self.bet_input.value}"

            # Update versus field
            versus_field_index = next((i for i, field in enumerate(embed.fields) if field.name == "⚔️ Versus"), None)
            if self.versus_input.value and self.versus_input.value.strip():
                if versus_field_index is not None:
                    embed.set_field_at(versus_field_index, name="⚔️ Versus", value=self.versus_input.value, inline=False)
                else:
                    embed.insert_field_at(0, name="⚔️ Versus", value=self.versus_input.value, inline=False)
            elif versus_field_index is not None:
                embed.remove_field(versus_field_index)

            # Update odds and units field
            details_field_index = next((i for i, field in enumerate(embed.fields) if field.name.startswith(":coin: Odds:")), None)
            if details_field_index is not None:
                embed.set_field_at(details_field_index, 
                    name=f":coin: Odds: **{self.odds_input.value}** - Units: **{self.units_input.value}u**", 
                    value=f"<@&{ROLE_ID}>", 
                    inline=True
                )

            # Update potential payout field
            payout_field_index = next((i for i, field in enumerate(embed.fields) if field.name == "💰 Potential Payout"), None)
            if payout_field_index is not None:
                potential_payout = float(self.units_input.value) * float(self.odds_input.value)
                embed.set_field_at(payout_field_index, name="💰 Potential Payout", value=f"**{potential_payout:.2f}u**", inline=True)

            # Update betslip field
            betslip_field_index = next((i for i, field in enumerate(embed.fields) if field.name == "📋 Betslip"), None)
            if self.betslip_input.value and self.betslip_input.value.strip():
                if betslip_field_index is not None:
                    embed.set_field_at(betslip_field_index, name="📋 Betslip", value=self.betslip_input.value, inline=False)
                else:
                    embed.add_field(name="📋 Betslip", value=self.betslip_input.value, inline=False)
            elif betslip_field_index is not None:
                embed.remove_field(betslip_field_index)

            await message.edit(embed=embed)

            await update_copied_message(self.sheet_row_number, message)

            await interaction.followup.send("✅ Bet updated successfully!", ephemeral=True)

        except Exception as e:
            print(f"ERROR in EditBetModal: {e}")
            await interaction.followup.send(f"❌ Error updating bet: {str(e)}", ephemeral=True)

@bot.tree.command(name="bet", description="Create a new bet")
async def bet_command(interaction: discord.Interaction):
    """Slash command to create a new bet"""
    try:
        modal = BetModal()
        await interaction.response.send_modal(modal)
    except Exception as e:
        print(f"ERROR in /bet command: {e}")
        await interaction.response.send_message("❌ Error creating bet form. Please try again.", ephemeral=True)

@bot.tree.command(name="stats", description="Show betting statistics")
async def stats_command(interaction: discord.Interaction):
    """Slash command to display betting statistics"""
    await interaction.response.defer(ephemeral=True)

    try:
        client = get_google_sheets_client()
        if not client:
            await interaction.followup.send("❌ Google Sheets connection error!", ephemeral=True)
            return

        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        worksheet = spreadsheet.get_worksheet(0)

        all_values = worksheet.get_all_values()

        if len(all_values) <= 1:
            await interaction.followup.send("📊 No betting data available yet.", ephemeral=True)
            return

        bets = []
        for i, row in enumerate(all_values[1:], start=2):
            if len(row) >= 10:
                try:
                    status = row[2] if len(row) > 2 else "Open"
                    odds = float(row[4]) if len(row) > 4 and row[4] else 0
                    units = float(row[5]) if len(row) > 5 and row[5] else 0
                    profit = float(row[9]) if len(row) > 9 and row[9] else 0
                    timestamp = row[8] if len(row) > 8 else ""

                    bets.append({
                        'status': status,
                        'odds': odds,
                        'units': units,
                        'profit': profit,
                        'timestamp': timestamp,
                        'row': i
                    })
                except (ValueError, IndexError) as e:
                    print(f"⚠️ Error processing row {i}: {e}")
                    continue

        if not bets:
            await interaction.followup.send("📊 No valid betting data found.", ephemeral=True)
            return

        # Calculate statistics
        total_bets = len(bets)
        won_bets = len([b for b in bets if b['status'] == 'Won'])
        lost_bets = len([b for b in bets if b['status'] == 'Lost'])
        draw_bets = len([b for b in bets if b['status'] == 'Draw'])
        open_bets = len([b for b in bets if b['status'] == 'Open'])

        total_staked = sum(b['units'] for b in bets if b['status'] in ['Won', 'Lost', 'Draw'])
        total_profit = sum(b['profit'] for b in bets if b['status'] in ['Won', 'Lost', 'Draw'])
        roi = (total_profit / total_staked * 100) if total_staked > 0 else 0

        # Create statistics embed
        embed = discord.Embed(
            title="📊 Betting Statistics",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )

        embed.add_field(
            name="📈 Overview",
            value=f"**Total Bets:** {total_bets}\n"
                  f"**Won:** {won_bets} | **Lost:** {lost_bets} | **Draw:** {draw_bets}\n"
                  f"**Open:** {open_bets}",
            inline=False
        )

        embed.add_field(
            name="💰 Profit & ROI",
            value=f"**Total Profit:** {total_profit:.2f}u\n"
                  f"**Total Staked:** {total_staked:.2f}u\n"
                  f"**ROI:** {roi:.2f}%",
            inline=False
        )

        if won_bets + lost_bets + draw_bets > 0:
            win_rate = (won_bets / (won_bets + lost_bets + draw_bets)) * 100
            embed.add_field(
                name="🎯 Win Rate",
                value=f"**{win_rate:.1f}%**",
                inline=True
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        print(f"ERROR in /stats command: {e}")
        await interaction.followup.send("❌ Error fetching statistics. Please try again.", ephemeral=True)

@bot.tree.command(name="graph", description="Generate profit graph")
async def graph_command(interaction: discord.Interaction):
    """Slash command to generate profit graph"""
    await interaction.response.defer(ephemeral=True)

    try:
        client = get_google_sheets_client()
        if not client:
            await interaction.followup.send("❌ Google Sheets connection error!", ephemeral=True)
            return

        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        worksheet = spreadsheet.get_worksheet(0)

        all_values = worksheet.get_all_values()

        if len(all_values) <= 1:
            await interaction.followup.send("📊 No betting data available for graph.", ephemeral=True)
            return

        # Process data for graph
        dates = []
        cumulative_profit = []
        current_profit = 0

        for row in all_values[1:]:
            if len(row) >= 10:
                try:
                    status = row[2] if len(row) > 2 else "Open"
                    profit_str = row[9] if len(row) > 9 else ""
                    timestamp_str = row[8] if len(row) > 8 else ""

                    if status in ['Won', 'Lost', 'Draw'] and profit_str:
                        profit = float(profit_str)
                        current_profit += profit

                        if timestamp_str:
                            try:
                                date = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                                dates.append(date)
                                cumulative_profit.append(current_profit)
                            except ValueError:
                                continue

                except (ValueError, IndexError) as e:
                    print(f"⚠️ Error processing row for graph: {e}")
                    continue

        if len(dates) < 2:
            await interaction.followup.send("📊 Not enough completed bets to generate graph.", ephemeral=True)
            return

        # Create graph
        plt.figure(figsize=(10, 6))
        plt.plot(dates, cumulative_profit, marker='o', linewidth=2, markersize=4)
        plt.title('Profit Over Time', fontsize=14, fontweight='bold')
        plt.xlabel('Date', fontsize=12)
        plt.ylabel('Cumulative Profit (u)', fontsize=12)
        plt.grid(True, alpha=0.3)
        plt.gcf().autofmt_xdate()

        # Format x-axis dates
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        plt.gca().xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))

        # Add zero line
        plt.axhline(y=0, color='red', linestyle='-', alpha=0.3)

        # Save to bytes buffer
        buffer = io.BytesIO()
        plt.savefig(buffer, format='png', dpi=150, bbox_inches='tight')
        buffer.seek(0)
        plt.close()

        # Send graph
        file = discord.File(buffer, filename='profit_graph.png')
        embed = discord.Embed(title="📈 Profit Over Time", color=discord.Color.green())
        embed.set_image(url="attachment://profit_graph.png")

        await interaction.followup.send(embed=embed, file=file, ephemeral=True)

    except Exception as e:
        print(f"ERROR in /graph command: {e}")
        await interaction.followup.send("❌ Error generating graph. Please try again.", ephemeral=True)

@bot.event
async def on_ready():
    """Event handler when bot is ready"""
    print(f'✅ {bot.user} has connected to Discord!')
    print(f'📊 Monitoring channel: {CHANNEL_ID}')
    print(f'🎯 Bot is ready with {len(bot.guilds)} guild(s)')

    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)