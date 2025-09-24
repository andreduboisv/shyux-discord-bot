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

# Configuration - SparkedHost uses environment variables or direct config
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', 'YOUR_BOT_TOKEN_HERE')
CHANNEL_ID = int(os.environ.get('CHANNEL_ID', '1420084335020740658'))
SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID', '1ZdZVIwUkBeEA6VFhS5lfZQbH9oZ1KHBeOIZPjbDDJoI')
ROLE_ID = int(os.environ.get('ROLE_ID', '935863068636897300'))
AUTHORIZED_ROLE_ID = int(os.environ.get('AUTHORIZED_ROLE_ID', '123456789012345678'))
DESTINATION_CHANNEL_ID = int(os.environ.get('DESTINATION_CHANNEL_ID', '1369427073567031436'))

# Google Sheets credentials - SparkedHost allows file uploads
GOOGLE_SHEETS_CREDENTIALS = 'credentials.json'

# Check if credentials file exists
if not os.path.exists(GOOGLE_SHEETS_CREDENTIALS):
    print("⚠️ credentials.json not found - creating placeholder")
    # Create minimal credentials to avoid crashes
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

# Google Sheets connection
def get_google_sheets_client():
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
    color_map = {
        "Open": discord.Color.blue(),
        "Won": discord.Color.green(),
        "Lost": discord.Color.red(),
        "Draw": discord.Color.gold()
    }
    return color_map.get(status, discord.Color.blue())

async def has_button_permission(interaction: discord.Interaction, sheet_row_number: int) -> bool:
    try:
        client = get_google_sheets_client()
        if not client:
            return False

        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        worksheet = spreadsheet.get_worksheet(0)
        creator_id = worksheet.cell(sheet_row_number, 1).value

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

        # Extract odds, units, and betslip
        odds = "?"
        units = "?"
        betslip = ""

        # Get odds and units from fields
        for field in original_embed.fields:
            if "Odds:" in field.name and "Units:" in field.name:
                odds_match = re.search(r"Odds:\s*\*\*([\d.]+)\*\*", field.name)
                units_match = re.search(r"Units:\s*\*\*([\d.]+)u\*\*", field.name)

                if odds_match:
                    odds = odds_match.group(1)
                if units_match:
                    units = units_match.group(1)

            # Extract betslip if available
            if field.name == "📋 Betslip":
                betslip = field.value

        # Create the formatted message with betslip if available (REMOVED EMOJI STATUS)
        formatted_message = f"<@&{ROLE_ID}>\n{bet_description} @{odds} - {units}u"

        if betslip:
            formatted_message += f"\n{betslip}"

        copy_message = await destination_channel.send(formatted_message)

        # Add dollar emoji reaction to the copy message
        try:
            await copy_message.add_reaction('💵')
        except Exception as e:
            print(f"⚠️ Could not add dollar emoji reaction: {e}")

        # Store the copy message ID in Google Sheets
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

        except Exception as e:
            print(f"❌ Error reading from Google Sheets: {e}")
            if original_message.embeds:
                original_embed = original_message.embeds[0]
                bet_description = original_embed.title.replace("🎯 ", "") if original_embed.title else "Unknown Bet"
                odds = "?"
                units = "?"
                betslip = ""

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
            else:
                bet_description = "Unknown Bet"
                odds = "?"
                units = "?"
                status = "Open"
                betslip = ""

        # Create the updated formatted message (REMOVED EMOJI STATUS)
        formatted_message = f"<@&{ROLE_ID}>\n{bet_description} @{odds} - {units}u"

        if betslip:
            formatted_message += f"\n{betslip}"

        await copy_message.edit(content=formatted_message)

        # Update reactions based on status
        try:
            # Remove all existing status emojis
            status_emojis = ['✅', '❌', '🔄']  # white_check_mark, x, repeat
            for emoji in status_emojis:
                async for user in copy_message.reactions:
                    if str(user.emoji) == emoji:
                        await copy_message.clear_reaction(emoji)
                        break

            # Add new status emoji based on current status
            status_emoji_map = {
                "Won": "✅",
                "Lost": "❌", 
                "Draw": "🔄"
            }
            
            if status in status_emoji_map:
                await copy_message.add_reaction(status_emoji_map[status])
                
        except Exception as e:
            print(f"⚠️ Error updating reactions: {e}")

        print(f"✅ Updated copy message for row {sheet_row_number} with status: {status}")
        return True

    except Exception as e:
        print(f"❌ Error updating copied message: {e}")
        return False

async def complete_bet(interaction: discord.Interaction, final_status: str, sheet_row_number: int):
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

        if final_status == "Won":
            profit = units * odds - units
            result_text = f"**+{profit:.2f}u**"
        elif final_status == "Lost":
            profit = -units
            result_text = f"{profit:.2f}u"
        else:
            profit = 0
            result_text = "0 U"

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

        embed = message.embeds[0]
        embed.description = f"**Status:** {final_status}"
        embed.color = get_status_color(final_status)

        field_names = [field.name for field in embed.fields]

        payout_field_index = next((i for i, field in enumerate(embed.fields) if field.name == "💰 Potential Payout"), None)
        if payout_field_index is not None:
            embed.remove_field(payout_field_index)
            field_names = [field.name for field in embed.fields]

        profit_field_index = next((i for i, field in enumerate(embed.fields) if field.name == "💰 Profit"), None)

        if profit_field_index is not None:
            embed.set_field_at(profit_field_index, name="💰 Profit", value=result_text, inline=True)
        else:
            details_field_index = next((i for i, field in enumerate(embed.fields) if field.name == "📊 Details"), None)
            if details_field_index is not None:
                embed.insert_field_at(details_field_index + 1, name="💰 Profit", value=result_text, inline=True)
            else:
                embed.add_field(name="💰 Profit", value=result_text, inline=True)

        view = View(timeout=None)
        view.add_item(UnlockButton(sheet_row_number))

        await message.edit(embed=embed, view=view)

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

            embed = message.embeds[0]
            embed.description = "**Status:** Open"
            embed.color = get_status_color("Open")

            field_names = [field.name for field in embed.fields]

            profit_field_index = next((i for i, field in enumerate(embed.fields) if field.name == "💰 Profit"), None)
            if profit_field_index is not None:
                embed.remove_field(profit_field_index)

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
            # Check permissions first
            if not await has_button_permission(interaction, self.sheet_row_number):
                await interaction.response.send_message(
                    "❌ You don't have permission to modify this bet!",
                    ephemeral=True
                )
                return

            # Send the modal directly without deferring
            modal = EditBetModal(self.sheet_row_number)
            await interaction.response.send_modal(modal)

        except Exception as e:
            print(f"Error in EditButton: {e}")
            await interaction.response.send_message(
                "❌ Error opening edit form. Please try again.",
                ephemeral=True
            )

class BetModal(Modal, title='Create New Bet'):
    def __init__(self):
        super().__init__()

        self.bet_input = TextInput(
            label='Bet',
            placeholder='Enter your bet description...',
            max_length=200,
            required=True
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
        self.add_item(self.odds_input)
        self.add_item(self.units_input)
        self.add_item(self.betslip_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        status = "Open"

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
                ""
            ]

            worksheet.append_row(row_data)

            all_values = worksheet.get_all_values()
            sheet_row_number = len(all_values)

            potential_payout = float(self.units_input.value) * float(self.odds_input.value)

            embed = discord.Embed(
                title=f"🎯 {self.bet_input.value}",
                description=f"Status: **{status}**",
                color=get_status_color(status),
                timestamp=datetime.now()
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

            view = View(timeout=None)
            view.add_item(WonButton(sheet_row_number))
            view.add_item(LostButton(sheet_row_number))
            view.add_item(DrawButton(sheet_row_number))
            view.add_item(EditButton(sheet_row_number))

            channel = bot.get_channel(CHANNEL_ID)
            if channel:
                message = await channel.send(embed=embed, view=view)

                worksheet.update_cell(sheet_row_number, 8, str(message.id))

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

            self.bet_input = TextInput(
                label='Bet*',
                default=row_data[3] if len(row_data) > 3 else "",
                max_length=200,
                required=True
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
            self.odds_input = TextInput(label='Odds*', default='1.0', max_length=10, required=True)
            self.units_input = TextInput(label='Units*', default='1.0', max_length=10, required=True)
            self.betslip_input = TextInput(label='Betslip', placeholder='Enter betslip info...', style=discord.TextStyle.paragraph, max_length=500, required=False)

        self.add_item(self.bet_input)
        self.add_item(self.odds_input)
        self.add_item(self.units_input)
        self.add_item(self.betslip_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            odds = float(self.odds_input.value)
            units = float(self.units_input.value)
            if odds <= 0 or units <= 0:
                raise ValueError("Values must be positive")
        except ValueError:
            await interaction.response.send_message("❌ Odds and Units must be valid positive numbers!", ephemeral=True)
            return

        try:
            client = get_google_sheets_client()
            if not client:
                await interaction.response.send_message("❌ Google Sheets connection error!", ephemeral=True)
                return

            spreadsheet = client.open_by_key(SPREADSHEET_ID)
            worksheet = spreadsheet.get_worksheet(0)

            updates = [
                (self.sheet_row_number, 4, self.bet_input.value),
                (self.sheet_row_number, 5, str(float(self.odds_input.value))),
                (self.sheet_row_number, 6, str(float(self.units_input.value))),
                (self.sheet_row_number, 7, self.betslip_input.value if self.betslip_input.value else "")
            ]

            for row, col, value in updates:
                worksheet.update_cell(row, col, value)

            message_id = worksheet.cell(self.sheet_row_number, 8).value
            if not message_id or message_id == "PENDING":
                await interaction.response.send_message("✅ Bet details updated in database!", ephemeral=True)
                return

            channel = bot.get_channel(CHANNEL_ID)
            if not channel:
                await interaction.response.send_message("✅ Bet details updated in database!", ephemeral=True)
                return

            try:
                message = await channel.fetch_message(int(message_id))

                if message and message.embeds:
                    embed = message.embeds[0]

                    field_names = [field.name for field in embed.fields]

                    if "📝 Bet" in field_names:
                        bet_index = field_names.index("📝 Bet")
                        embed.set_field_at(bet_index, name="📝 Bet", value=self.bet_input.value, inline=False)

                    if "📊 Details" in field_names:
                        details_index = field_names.index("📊 Details")
                        embed.set_field_at(details_index, name="📊 Details",
                                         value=f"**Odds:** {self.odds_input.value}\n**Units:** {self.units_input.value}",
                                         inline=True)

                    if "💰 Potential Payout" in field_names:
                        payout_index = field_names.index("💰 Potential Payout")
                        try:
                            payout = float(self.units_input.value) * float(self.odds_input.value)
                            embed.set_field_at(payout_index, name="💰 Potential Payout", value=f"{payout:.2f} units", inline=True)
                        except:
                            pass

                    if "📋 Betslip" in field_names:
                        betslip_index = field_names.index("📋 Betslip")
                        if self.betslip_input.value and self.betslip_input.value.strip():
                            embed.set_field_at(betslip_index, name="📋 Betslip", value=self.betslip_input.value, inline=False)
                        else:
                            embed.remove_field(betslip_index)
                    elif self.betslip_input.value and self.betslip_input.value.strip():
                        embed.add_field(name="📋 Betslip", value=self.betslip_input.value, inline=False)

                    await message.edit(embed=embed)

                    await update_copied_message(self.sheet_row_number, message)

            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                print(f"Warning: Could not update message: {e}")

            await interaction.response.send_message("✅ Bet details updated successfully!", ephemeral=True)

        except Exception as e:
            print(f"ERROR in EditBetModal onSubmit: {e}")
            await interaction.response.send_message(f"❌ Error updating bet details: {str(e)}", ephemeral=True)

@bot.tree.command(name="bet", description="Create a new bet")
async def bet_command(interaction: discord.Interaction):
    modal = BetModal()
    await interaction.response.send_modal(modal)

@bot.tree.command(name="stats", description="Show betting statistics - overall or for specific month")
@app_commands.describe(
    month="Select month in YYYY-MM or YYYY-M format (optional, e.g., 2024-01 or 2024-1)",
    chart="Include balance progression chart (default: false)"
)
async def stats_command(interaction: discord.Interaction, month: Optional[str] = None, chart: bool = False):
    try:
        await interaction.response.defer(thinking=True, ephemeral=False)

        client = get_google_sheets_client()
        if not client:
            await interaction.followup.send("❌ Google Sheets connection error!")
            return

        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        worksheet = spreadsheet.get_worksheet(0)

        all_data = worksheet.get_all_values()
        if len(all_data) <= 1:
            await interaction.followup.send("❌ No betting data found!")
            return

        COL_STATUS = 2
        COL_ODDS = 4
        COL_UNITS = 5
        COL_TIMESTAMP = 8
        COL_PROFIT = 9

        user_data = []
        status_count = {'Won': 0, 'Lost': 0, 'Draw': 0, 'Open': 0, 'Other': 0}

        for i, row in enumerate(all_data[1:], start=2):
            try:
                if len(row) > COL_TIMESTAMP and row[COL_TIMESTAMP]:
                    bet_date = datetime.strptime(row[COL_TIMESTAMP], '%Y-%m-%d %H:%M:%S')

                    profit = 0.0
                    if len(row) > COL_PROFIT and row[COL_PROFIT]:
                        try:
                            profit = float(row[COL_PROFIT])
                        except (ValueError, TypeError):
                            pass

                    units = 0.0
                    if len(row) > COL_UNITS and row[COL_UNITS]:
                        try:
                            units = float(row[COL_UNITS])
                        except (ValueError, TypeError):
                            pass

                    odds = 0.0
                    if len(row) > COL_ODDS and row[COL_ODDS]:
                        try:
                            odds = float(row[COL_ODDS])
                        except (ValueError, TypeError):
                            pass

                    status = row[COL_STATUS] if len(row) > COL_STATUS else "Unknown"

                    if status in status_count:
                        status_count[status] += 1
                    else:
                        status_count['Other'] += 1

                    user_data.append({
                        'date': bet_date,
                        'profit': profit,
                        'status': status,
                        'units': units,
                        'odds': odds,
                        'row_number': i,
                        'raw_status': status
                    })
            except (ValueError, TypeError) as e:
                continue

        if not user_data:
            await interaction.followup.send("❌ No betting data found!")
            return

        user_data.sort(key=lambda x: x['date'])

        if month:
            try:
                # Fix month format: handle both YYYY-MM and YYYY-M
                if len(month.split('-')[1]) == 1:  # If month is single digit (e.g., 2025-1)
                    month = f"{month.split('-')[0]}-{month.split('-')[1].zfill(2)}"  # Convert to 2025-01

                selected_date = datetime.strptime(month, "%Y-%m")
                user_data = [bet for bet in user_data if bet['date'].strftime('%Y-%m') == month]

                if not user_data:
                    await interaction.followup.send(f"❌ No betting data found for {month}!")
                    return

                title = f"📊 Betting Statistics - {month}"
            except ValueError:
                await interaction.followup.send("❌ Invalid month format! Use YYYY-MM or YYYY-M (e.g., 2024-01 or 2024-1)")
                return
        else:
            title = "📊 Overall Betting Statistics"

        won_bets = len([bet for bet in user_data if bet['status'] == 'Won'])
        lost_bets = len([bet for bet in user_data if bet['status'] == 'Lost'])
        settled_bets = [bet for bet in user_data if bet['status'] in ['Won', 'Lost']]

        total_profit = sum(bet['profit'] for bet in user_data)
        total_staked = sum(bet['units'] for bet in user_data)

        winrate = (won_bets / (won_bets + lost_bets)) * 100 if (won_bets + lost_bets) > 0 else 0
        avg_odds = sum(bet['odds'] for bet in settled_bets) / len(settled_bets) if settled_bets else 0

        # Calculate ROI (%) = (Total Profit / Total Staked) * 100
        roi = (total_profit / total_staked * 100) if total_staked > 0 else 0

        ending_balance = 100.0 + total_profit

        chart_file = None
        if chart:
            dates = []
            cumulative_profit = []
            current_balance = 100.0

            settled_bets_for_chart = [bet for bet in user_data if bet['status'] in ['Won', 'Lost', 'Draw']]

            for bet in settled_bets_for_chart:
                dates.append(bet['date'])
                current_balance += bet['profit']
                cumulative_profit.append(current_balance)

            if len(cumulative_profit) > 1:
                try:
                    plt.figure(figsize=(10, 6))
                    plt.plot(dates, cumulative_profit, marker='o', linewidth=2, markersize=4, color='#5865F2')

                    plt.title('Balance Progression', fontsize=14, fontweight='bold')
                    plt.xlabel('Date')
                    plt.ylabel('Balance (u)')
                    plt.grid(True, alpha=0.3)

                    if month:
                        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%d'))
                        plt.gca().xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(dates)//5)))
                    else:
                        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
                        plt.gca().xaxis.set_major_locator(mdates.MonthLocator())

                    plt.gcf().autofmt_xdate()
                    plt.tight_layout()

                    buffer = io.BytesIO()
                    plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight')
                    buffer.seek(0)
                    chart_file = discord.File(buffer, filename='chart.png')
                    plt.close()

                except Exception as e:
                    chart_file = None

        embed_color = discord.Color.green() if total_profit >= 0 else discord.Color.red()
        embed = discord.Embed(title=title, color=embed_color)

        if chart_file:
            embed.set_image(url="attachment://chart.png")

        # 3x3 grid layout with different emojis
        # Row 1: Winrate, Won, Lost
        embed.add_field(name="🎯 Winrate", value=f"**{winrate:.1f}%**", inline=True)
        embed.add_field(name="✅ Won", value=f"**{won_bets}**", inline=True)
        embed.add_field(name="❌ Lost", value=f"**{lost_bets}**", inline=True)

        # Row 2: Profit, AVG Odds, Staked
        embed.add_field(name="💰 Profit", value=f"**{total_profit:+.2f}u**", inline=True)
        embed.add_field(name="⚖️ AVG Odds", value=f"**{avg_odds:.2f}**", inline=True)
        embed.add_field(name="🎰 Staked", value=f"**{total_staked:.2f}u**", inline=True)

        # Row 3: ROI, Starting, Ending
        embed.add_field(name="📈 ROI", value=f"**{roi:+.1f}%**", inline=True)
        embed.add_field(name="🏦 Starting", value=f"**100.00u**", inline=True)
        embed.add_field(name=":coin: Ending", value=f"**{ending_balance:.2f}u**", inline=True)

        if chart and chart_file:
            await interaction.followup.send(embed=embed, file=chart_file)
        else:
            await interaction.followup.send(embed=embed)

    except Exception as e:
        print(f"ERROR in stats command: {e}")
        await interaction.followup.send("❌ Error retrieving statistics. Please try again later.")

@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    print('✅ Bot hosted on SparkedHost')
    print('✅ Server: 24/7 uptime guaranteed')

if __name__ == "__main__":
    if not DISCORD_TOKEN or DISCORD_TOKEN == 'YOUR_BOT_TOKEN_HERE':
        print("❌ ERROR: DISCORD_TOKEN not set!")
        print("💡 Set it in SparkedHost panel -> Startup -> Environment Variables")
    else:
        try:
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            print(f"❌ ERROR starting bot: {e}")
