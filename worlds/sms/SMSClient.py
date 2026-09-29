from __future__ import annotations

import copy
import os
import sys
import asyncio
import collections
import time
import traceback
from typing import Optional
from dataclasses import dataclass

import ModuleUpdate
import Utils
from NetUtils import ClientStatus
from CommonClient import gui_enabled, logger, get_base_parser, ClientCommandProcessor, \
    server_loop
from .options import SmsOptions
from .bit_helper import change_endian, bit_flagger, extract_bits
from .regions import ALL_REGIONS, get_location_name_to_id
from .items import TICKET_ITEMS
import dolphin_memory_engine as dme
import websockets
from . import addresses
from settings import get_settings

ModuleUpdate.update()

TRACKER_LOADED = False
try:
    from worlds.tracker.TrackerClient import TrackerGameContext as SuperContext
    TRACKER_LOADED = True
except ModuleNotFoundError:
    from CommonClient import CommonContext as SuperContext

CONNECTION_REFUSED_GAME_STATUS = (
    "Dolphin failed to connect. Please load a randomized ROM for Super Mario Sunshine. Trying again in 5 seconds..."
)
CONNECTION_REFUSED_SAVE_STATUS = (
    "Dolphin failed to connect. Please load into the save file. Trying again in 5 seconds..."
)
CONNECTION_LOST_STATUS = (
    "Dolphin connection was lost. Please restart your emulator and make sure Super Mario Sunshine is running."
)
CONNECTION_CONNECTED_STATUS = "Dolphin connected successfully."
CONNECTION_INITIAL_STATUS = "Dolphin connection has not been initiated."

ticket_listing = []
world_flags = {}

DEBUG = False
GAME_VER = 0x3a
AP_WORLD_VERSION_NAME = "0.6.7"
CLIENT_VERSION = "0.6.3-alelau18"

DME_DOLPHIN_PROCESS_NAME_ENV_VARIABLE = "DME_DOLPHIN_PROCESS_NAME"

INITIATED_1_UPS = False

@dataclass
class NozzleItem:
    nozzle_name: str
    ap_item_id: int


NOZZLES: list[NozzleItem] = [
    NozzleItem("Spray Nozzle", 523000),
    NozzleItem("Hover Nozzle", 523001),
    NozzleItem("Rocket Nozzle", 523002),
    NozzleItem("Turbo Nozzle", 523003),
    NozzleItem("Yoshi", 523013)
]

class SmsCommandProcessor(ClientCommandProcessor):
    def _cmd_dolphin(self):
        """Prints the current Dolphin connection status."""
        if isinstance(self.ctx, SmsContext):
            logger.info(f"Dolphin Status: {self.ctx.dolphin_status}")

    def _cmd_resync(self):
        """Manually trigger a resync."""
        self.output("Syncing items.")
        self.ctx.locations_sent = set()
        Utils.async_start(self.ctx.send_msgs([{"cmd": "Sync"}]))
        if dolphin_ready(self.ctx):
            refresh_collection_counts(self.ctx)

    def _cmd_change_dolphin_process_name(self, process_name: str):
        """Specify the name of the Dolphin process to connect to. "" for system default."""
        self.ctx.hook_name = process_name
        logger.info(f"Changing Dolphin process name to: {process_name if process_name else ''}")
        from . import SuperMarioSunshineSettings
        settings: SuperMarioSunshineSettings = get_settings().sms_options
        settings.dolphin_process_name = SuperMarioSunshineSettings.DolphinProcessName(process_name)
        get_settings().save()
        log_msg: str = f"Dolphin process name set to {process_name or 'default'}. You must open a new client for this to take effect."
        logger.info(log_msg)
        Utils.messagebox("Close SMS Client to take effect", log_msg)
        Utils.async_start(unhook_dolphin(self.ctx))

class SmsContext(SuperContext):
    command_processor = SmsCommandProcessor
    game = "Super Mario Sunshine"
    tags = {"AP"}
    items_handling = 0b111  # full remote

    options: SmsOptions
    hook_name: str = ""

    lives_given = 0
    lives_switch = False

    plaza_episode = 0

    goal = 50
    corona_message_given = False
    blue_status = 1
    fludd_start = 0
    bianco_flag = 0
    ticket_mode = False
    victory = False
    checked_yoshi_egg = False

    # Current Shine/Blue Coins and Recv Shine/Blue Coin
    curr_shines: int = 0
    req_shine: int = 0
    curr_blue_coins: int = 0
    req_blue_coins: int = 0

    def __init__(self, server_address, password):
        super(SmsContext, self).__init__(server_address, password)
        self.send_index: int = 0
        self.awaiting_bridge = False
        self.dolphin_sync_task: Optional[asyncio.Task[None]] = None
        self.dolphin_status: str = CONNECTION_INITIAL_STATUS
        self.awaiting_rom: bool = False
        self.has_send_death: bool = False
        # A received DeathLink that hasn't killed Mario yet. It stays set until Mario dies, so a
        # death received in a menu, a pause or a load still lands once he's back in a stage.
        self.pending_kill: bool = False
        # Whether the pending kill was written to the game, so the death it causes isn't sent back.
        self.kill_written: bool = False
        # Times of the deaths we sent, to recognise them when the server bounces them back to us.
        self.sent_death_times: collections.deque[float] = collections.deque(maxlen=16)
        # Mario died while the death couldn't be sent (AP connection down); sent once it's back.
        self.unsent_death: bool = False
        # (seed, slot) the DeathLink state above belongs to.
        self.death_link_owner: tuple = ()
        self.num_1_ups_current: int = 0
        self.ap_nozzles_received: list[int] = []
        # Location ids already sent to the server this connection; only the difference is sent.
        self.locations_sent: set[int] = set()
        self.last_pending_resend: float = 0.0
        # Last map/episode ids stored on the server, so each is only sent when it changes.
        self.last_map_id: Optional[int] = None
        self.last_episode_id: Optional[int] = None
        # Received-item counts, recomputed only when items_received changes (see get_item_counts).
        self._item_counts: Optional[collections.Counter[int]] = None
        self._ui_state: tuple = ()

        from . import SuperMarioSunshineSettings
        settings: SuperMarioSunshineSettings = get_settings().sms_options
        if settings.dolphin_process_name:
            os.environ[DME_DOLPHIN_PROCESS_NAME_ENV_VARIABLE] = settings.dolphin_process_name
        elif DME_DOLPHIN_PROCESS_NAME_ENV_VARIABLE in os.environ:
            del os.environ[DME_DOLPHIN_PROCESS_NAME_ENV_VARIABLE]

    async def server_auth(self, password_requested: bool = False):
        if password_requested and not self.password:
            await super(SmsContext, self).server_auth(password_requested)
        await self.get_username()
        await self.send_connect()

    @property
    def endpoints(self):
        if self.server:
            return [self.server]
        else:
            return []

    def on_package(self, cmd: str, args: dict):
        old_watcher: Optional[asyncio.Task] = getattr(self, "watcher_task", None)

        super().on_package(cmd, args)

        # Universal Tracker starts a new watcher_task on every Connected without cancelling the
        # previous one, so each reconnect added another full logic rebuild per item update.
        if (cmd == "Connected" and old_watcher is not None and not old_watcher.done()
                and getattr(self, "watcher_task", None) is not old_watcher):
            old_watcher.cancel()

        if cmd in ("ReceivedItems", "Connected"):
            # items_received was just rebuilt or appended to, or belongs to a new slot.
            self._item_counts = None

        if cmd == "Connected":
            self.last_map_id = None
            self.last_episode_id = None
            # Resend every known check once after a (re)connect; the server already sends the
            # full ReceivedItems list on Connected, so no Sync is needed here.
            self.locations_sent = set()
            slot_data = args.get("slot_data")
            # Seeds generated before the Total Shines rework (0.6.2) only have corona_mountain_shines.
            self.goal = slot_data.get("required_shines", slot_data.get("corona_mountain_shines"))
            # These options' first value is 0, which is falsy - compare against None so
            # option value 0 doesn't silently keep the class default.
            temp = slot_data.get("blue_coin_sanity")
            if temp is not None:
                self.blue_status = temp
            temp = slot_data.get("starting_nozzle")
            if temp is not None:
                self.fludd_start = temp
            temp = slot_data.get("level_access")
            if temp is not None:
                self.ticket_mode = temp

            self.req_shine = self.get_corona_goal()
            self.req_blue_coins = slot_data.get("blue_coin_maximum", 0)

            if "death_link" in slot_data:
                Utils.async_start(self.update_death_link(bool(slot_data["death_link"])))

            # DeathLink state carries over reconnects, but not into another seed or slot.
            owner = (self.seed_name, self.slot)
            if owner != self.death_link_owner:
                self.death_link_owner = owner
                self.pending_kill = False
                self.kill_written = False
                self.unsent_death = False


    def on_deathlink(self, data: dict):
        if data.get("time") in self.sent_death_times:
            # Our own death bounced back. The base class only filters the latest one it sent.
            return
        super().on_deathlink(data)
        source = data.get('source', 'Unknown')
        cause = data.get('cause', 'No cause specified')
        logger.info(f"DeathLink received! Source: {source}")
        logger.info(f"DeathLink message: {cause}")
        logger.info("Killing Mario as soon as he's in a stage...")
        # check_death applies it on the next watcher tick, or once Mario is playable again.
        self.pending_kill = True

    async def send_death(self, death_text: str = ""):
        # Same as the base class, but the time is remembered before sending: another player's death
        # handled while this one is sent moves last_death_link, and the echo would kill Mario again.
        if self.server and self.server.socket:
            logger.info("DeathLink: Sending death to your friends...")
            self.last_death_link = time.time()
            self.sent_death_times.append(self.last_death_link)
            await self.send_msgs([{
                "cmd": "Bounce", "tags": ["DeathLink"],
                "data": {
                    "time": self.last_death_link,
                    "source": self.player_names[self.slot],
                    "cause": death_text
                }
            }])

    def get_item_counts(self) -> collections.Counter[int]:
        """Received-item counts by item id, cached until the next ReceivedItems packet."""
        if self._item_counts is None:
            self._item_counts = collections.Counter(item.item for item in self.items_received)
        return self._item_counts

    def get_corona_goal(self):
        # A goal of 0 is a legal option value - only fall back when it is missing.
        return self.goal if self.goal is not None else 50

    def make_gui(self):
        # Performing local import to prevent additional UIs to appear during the patching process.
        # This appears to be occurring if a spawned process does not have a UI element when importing kvui/kivymd.
        from .sms_tab import build_gui, GameManager, MDLabel

        ui: type[GameManager] = super().make_gui()
        class SMSGuiWrapper(ui):
            shine_count: MDLabel
            blue_coins: MDLabel
            tickets: MDLabel
            base_title = "Super Mario Sunshine Client"

            def build(self):
                container = super().build()

                self.base_title += " |  Archipelago"
                build_gui(self)

                return container

            def update_corona_shine_count(self, shine_count: int, shines_required: int):
                self.shine_count.text = f"{shine_count} / {shines_required}"

            def update_blue_coins(self, blue_coins: int, coins_req: int):
                self.blue_coins.text = f"{blue_coins} / {coins_req}"

            def update_ticket_list(self, ticket_list: list[str]):
                self.tickets.text = "; ".join(ticket_list)

        return SMSGuiWrapper


storedShines = []
curShines = []
storedBlues = []
curBlues = []
storedNozzleBoxes = []
curNozzleBoxes = []

DELAY_SECONDS = .5
PENDING_RESEND_SECONDS = 10
# Built once; get_location_name_to_id() rebuilds the whole table on every call.
LOCATION_NAME_TO_ID: dict[str, int] = get_location_name_to_id()

MEM1_START = 0x80000000
MEM1_END = 0x81800000
FILE_SELECT_STAGE = 15
DIRECTOR_SIZE = 0x264
# TMarDirector mState values
DIRECTOR_STATE_PLAYING = 4
DIRECTOR_STATE_MISS = 7  # Mario died: the "Too Bad!" sequence
# TMarDirector mFlags bits. In normal play only the idle bits (shine taken, first/last
# simulation tick of the frame) are set; any other bit is an event waiting to be handled.
DIRECTOR_FLAG_GAME_OVER_PENDING = 0x20
DIRECTOR_IDLE_FLAGS = 0xE000


def read_string(console_address: int, strlen: int) -> str:
    return dme.read_bytes(console_address, strlen).split(b"\0", 1)[0].decode()


def game_start():
    for _ in range(0, addresses.SMS_SHINE_BYTE_COUNT):
        storedShines.append(0x00)
        curShines.append(0x00)
    for _ in range(0, addresses.SMS_BLUECOIN_BYTE_COUNT):
        storedBlues.append(0x00)
        curBlues.append(0x00)
    for _ in range(0, addresses.NOZZLE_BOXES_BYTE_COUNT):
        storedNozzleBoxes.append(0x00)
        curNozzleBoxes.append(0x00)

# Apparently when you beat the game it considers current stage AS FILE SELECT
# Therefore it wasn't sending out the Victory check
def in_file_select():
    return dme.read_byte(addresses.SMS_CURRENT_STAGE) == 15


def dolphin_connected(ctx: SmsContext) -> bool:
    """Whether Dolphin is hooked into the SMS AP game."""
    return ctx.dolphin_status == CONNECTION_CONNECTED_STATUS and dme.is_hooked()


def dolphin_ready(ctx: SmsContext) -> bool:
    """Whether Dolphin is hooked into the SMS AP game and the client is in a slot."""
    return ctx.slot is not None and dolphin_connected(ctx)


def dolphin_read_failed(ctx: SmsContext, message: str) -> None:
    """Drop the hook after a failed memory read so dolphin_sync_task re-hooks, logging once."""
    if ctx.dolphin_status == CONNECTION_CONNECTED_STATUS:
        logger.error(message)
        logger.info("Connection to Dolphin lost, reconnecting...")
    ctx.dolphin_status = CONNECTION_LOST_STATUS
    dme.un_hook()


async def game_watcher(ctx: SmsContext):
    while not ctx.exit_event.is_set():
        if not dolphin_connected(ctx):
            await asyncio.sleep(1)
            continue

        # if in_file_select():
        #     await asyncio.sleep(1)
        #     continue

        # A failed Dolphin read must not end this task for the rest of the session: the client
        # would stay connected but silently stop sending checks and writing items.
        try:
            if ctx.slot is not None:
                await game_watcher_tick(ctx)
            elif "DeathLink" in ctx.tags:
                # While the AP connection is down, still apply received DeathLinks and notice deaths.
                await check_death(ctx)
            else:
                await asyncio.sleep(0.8)
        except Exception as e:
            dolphin_read_failed(ctx, f"SMS game watcher error: {e}")

        await asyncio.sleep(0.2)
        ctx.lives_switch = False


async def game_watcher_tick(ctx: SmsContext):
    await handle_stages(ctx)
    await location_watcher(ctx)

    if "DeathLink" in ctx.tags:
        await check_death(ctx)

    # Sending Sync + every checked location each tick made the server stream the whole
    # ReceivedItems list back five times a second. Each packet re-runs Universal Tracker's
    # update, which leaks memory in its GUI, so long sessions grew to many GB (issue #66).
    # Sync only on /resync, and only send checks that are new.
    # Only locations this slot has; e.g. a boathouse trade without blue coin sanity maps to a
    # location id the server doesn't know, and would otherwise be re-sent forever.
    new_locations = (ctx.locations_checked & ctx.server_locations) - ctx.locations_sent
    if new_locations:
        ctx.locations_sent |= new_locations
        ctx.last_pending_resend = time.monotonic()
        await ctx.send_msgs([{"cmd": "LocationChecks", "locations": list(new_locations)}])
    elif time.monotonic() - ctx.last_pending_resend > PENDING_RESEND_SECONDS:
        # Re-send checks the server still hasn't confirmed, in case a send was dropped. This
        # doesn't trigger a ReceivedItems reply, so it can't bring the old packet storm back.
        ctx.last_pending_resend = time.monotonic()
        pending = (ctx.locations_checked & ctx.server_locations) - ctx.checked_locations
        if pending:
            await ctx.send_msgs([{"cmd": "LocationChecks", "locations": list(pending)}])

    #Gravi01 Begin
    refresh_collection_counts(ctx)
    ctx.lives_switch = True
    #Gravi01 End

    if ctx.victory and not ctx.finished_game:
        await ctx.send_msgs([{"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}])
        ctx.finished_game = True


async def check_death(ctx: SmsContext):
    """Send a DeathLink when Mario dies, and kill Mario for a received one once he's playable."""
    if ctx.unsent_death:
        await send_mario_death(ctx)

    director = read_director()
    if director is None:
        return
    game_state = dme.read_byte(director + addresses.DIRECTOR_STATE_OFFSET)

    if game_state == DIRECTOR_STATE_MISS:
        # Only send a death that is ours, not the one a received DeathLink caused.
        if not ctx.has_send_death and not ctx.kill_written:
            ctx.unsent_death = True
            await send_mario_death(ctx)

        ctx.has_send_death = True
        # Whatever DeathLink was pending is satisfied by this death.
        ctx.pending_kill = False
        ctx.kill_written = False

    elif game_state == DIRECTOR_STATE_PLAYING:
        # Mario's own death animation also plays in this state before the game switches to the death
        # state. A kill written then lands as that death, which counts as the received one and isn't
        # sent: both deaths are covered by the one DeathLink, like a DeathLink arriving mid-death.
        # Allows for death links to be sent once respawned
        ctx.has_send_death = False
        if ctx.pending_kill and kill_mario(director):
            ctx.kill_written = True


async def location_watcher(ctx):
    flags_ptr = dme.read_word(addresses.SMS_FLAGS_PTR)
    for x in range(0, addresses.SMS_SHINE_BYTE_COUNT):
        targ_location = flags_ptr + x
        cache_byte = dme.read_byte(targ_location)
        curShines[x] = cache_byte
        if storedShines[x] != curShines[x]:
            memory_changed(ctx, x, curShines[x], "Shine")
            storedShines[x] = curShines[x]

    # If possible, check if blue coin sanity is enabled or not
    for x in range(0, addresses.SMS_BLUECOIN_BYTE_COUNT):
        targ_location = flags_ptr + addresses.BLUECOIN_LOC_OFFSET + x
        cache_byte = dme.read_byte(targ_location)
        curBlues[x] = cache_byte
        if storedBlues[x] != curBlues[x]:
            memory_changed(ctx, x+15, curBlues[x], "Blue Coin") # Add 15 to 'x' to align with blue coin IDs
            storedBlues[x] = curBlues[x]

    for x in range(0, addresses.NOZZLE_BOXES_BYTE_COUNT):
        targ_location = flags_ptr + addresses.NOZZLE_BOXES_OFFSET + x
        cache_byte = dme.read_byte(targ_location)
        curNozzleBoxes[x] = cache_byte
        if storedNozzleBoxes[x] != curNozzleBoxes[x]:
            memory_changed(ctx, x+108, curNozzleBoxes[x], "Nozzle")
            storedNozzleBoxes[x] = curNozzleBoxes[x]

    # Check corresponds to Shadow Mario Yoshi Egg Chase
    delfino_yoshi_unlock = dme.read_byte(flags_ptr + addresses.DELFINO_YOSHI_OFFSET)
    if (delfino_yoshi_unlock & 0x80) and not ctx.checked_yoshi_egg:
        ctx.checked_yoshi_egg = True
        memory_changed(ctx, 113, delfino_yoshi_unlock, "Yoshi")
    return


async def handle_stages(ctx):
    #Gravi01  change to connection status
    next_stage = dme.read_byte(addresses.SMS_NEXT_STAGE)
    cur_stage = dme.read_byte(addresses.SMS_CURRENT_STAGE)
    current_episode = dme.read_byte(addresses.SMS_CURRENT_EPISODE)
    next_episode = dme.read_byte(addresses.SMS_NEXT_EPISODE)
    if next_stage == 0x01: # Delfino Plaza
        # If starting Fluddless without ticket mode on, open Bianco Hills
        if not ctx.bianco_flag and ctx.fludd_start == 2 and ctx.ticket_mode == 0:
            ctx.bianco_flag |= dme.read_byte(TICKETS[0].address)
            dme.write_byte(TICKETS[0].address, ctx.bianco_flag)
            open_stage(TICKETS[0])
        # Sets plaza state to 8 if in ticket mode and goal hasn't been reached
        if ctx.ticket_mode == 1 and next_episode != 0x8 and not ctx.corona_message_given:
            # Should change this to be flag based, set the flags necessary to load plaza 8 regardless
            dme.write_byte(addresses.SMS_NEXT_EPISODE, 8)
    
    if cur_stage == next_stage:
        # No-op unless the stored value is stale (e.g. right after a reconnect).
        await send_map_id(cur_stage, ctx)
    else:
        await send_map_id(next_stage, ctx)

        if ctx.ticket_mode:
            await resolve_tickets(next_stage, ctx)

    if (next_stage < 0x0D and next_stage != 0x07) and (next_episode != current_episode) and (next_episode != 0xFF):
        next_episode = dme.read_byte(addresses.SMS_NEXT_EPISODE)

        if next_stage == 0x01 or next_stage >= 0x0D:
            await send_episode_id(-1, ctx)
        else:
            await send_episode_id(next_episode, ctx)


async def dolphin_sync_task(ctx: SmsContext) -> None:
    logger.info("Starting Dolphin connector. Use /dolphin for status information.")
    while not ctx.exit_event.is_set():
        try:
            if dme.is_hooked() and ctx.dolphin_status == CONNECTION_CONNECTED_STATUS:
                if dme.read_bytes(0x80000000, 6) != b"GMSEAP":
                    set_dolphin_status(ctx, CONNECTION_REFUSED_GAME_STATUS)
                    continue
                # if ctx.slot is not None:
                #     # await give_items(ctx)
                #     # await check_locations(ctx)
                #     # await check_current_stage_changed(ctx)
                #     # self._cmd_resync()
                # else:
                if ctx.awaiting_rom:
                    await ctx.server_auth()

                # If the client's ui has loaded
                if ctx.ui:
                    counts = ctx.get_item_counts()
                    ctx.curr_shines = counts[523004]
                    ctx.curr_blue_coins = counts[523014]
                    ticket_list: list[str] = []
                    if ctx.ticket_mode:
                        ticket_list = [name.replace(" Ticket", "")
                                       for name, item_id in TICKET_ITEMS.items() if counts[item_id]]
                    # Only touch the labels when something changed, not ten times a second.
                    ui_state = (ctx.curr_shines, ctx.req_shine, ctx.curr_blue_coins, ctx.req_blue_coins,
                                tuple(ticket_list))
                    if ui_state != ctx._ui_state:
                        ctx._ui_state = ui_state
                        ctx.ui.update_corona_shine_count(ctx.curr_shines, ctx.req_shine)
                        ctx.ui.update_blue_coins(ctx.curr_blue_coins, ctx.req_blue_coins)
                        if ctx.ticket_mode:
                            ctx.ui.update_ticket_list(ticket_list)

                    if ctx.ticket_mode:
                        flag_pointer = dme.read_word(addresses.SMS_FLAGS_PTR)
                        boat_and_yoshi_flags = dme.read_byte(flag_pointer + addresses.DELFINO_YOSHI_OFFSET)
                        dme.write_byte(flag_pointer + addresses.DELFINO_YOSHI_OFFSET, boat_and_yoshi_flags | 0x02)

                await asyncio.sleep(0.1)
            else:
                if ctx.dolphin_status == CONNECTION_CONNECTED_STATUS:
                    logger.info("Connection to Dolphin lost, reconnecting...")
                    ctx.dolphin_status = CONNECTION_LOST_STATUS
                # Stay hooked while Dolphin runs another game: each hook() opens a new process
                # handle that un_hook() never closes (Windows), so re-hooking every 5s leaked a
                # handle per attempt.
                if not dme.is_hooked():
                    dme.hook()
                if dme.is_hooked():
                    if dme.read_bytes(0x80000000, 6) != b"GMSEAP":
                        set_dolphin_status(ctx, CONNECTION_REFUSED_GAME_STATUS)
                        await asyncio.sleep(5)
                    else:
                        set_dolphin_status(ctx, CONNECTION_CONNECTED_STATUS)
                        # Re-scan every flag after a re-hook. locations_checked is kept, so a
                        # check whose send was lost is still re-sent.
                        for cache in (storedShines, storedBlues, storedNozzleBoxes):
                            cache[:] = [0x00] * len(cache)
                        await asyncio.sleep(5)
                else:
                    set_dolphin_status(ctx, CONNECTION_LOST_STATUS)
                    await asyncio.sleep(5)
                    continue
        except Exception:
            if ctx.dolphin_status == CONNECTION_CONNECTED_STATUS:
                logger.info("Connection to Dolphin failed, attempting again in 5 seconds...")
                logger.error(traceback.format_exc())
            ctx.dolphin_status = CONNECTION_LOST_STATUS
            await unhook_dolphin(ctx)
            await asyncio.sleep(5)
            continue


def set_dolphin_status(ctx: SmsContext, status: str) -> None:
    """Log the Dolphin status only when it changes, not on every 5s retry."""
    if ctx.dolphin_status != status:
        logger.info(status)
    ctx.dolphin_status = status


async def unhook_dolphin(ctx: SmsContext):
    # Only drop the Dolphin hook. Disconnecting from the AP server here marked the disconnect as
    # intentional, so the client never reconnected and every Dolphin hiccup cost the session.
    dme.un_hook()

async def arbitrary_ram_checks(ctx):
    while not ctx.exit_event.is_set():
        if not dolphin_ready(ctx):
            await asyncio.sleep(1)
            continue

        try:
            activated_bits = dme.read_byte(addresses.ARB_NOZZLES_ENABLER)

            for noz in ctx.ap_nozzles_received:
                if noz < 4:
                    activated_bits = bit_flagger(activated_bits, noz, True)
                    dme.write_byte(addresses.ARB_FLUDD_ENABLER, 0x1)
                    dme.write_byte(addresses.ARB_NOZZLES_ENABLER, activated_bits)
        except Exception as e:
            dolphin_read_failed(ctx, f"SMS nozzle watcher error: {e}")
        await asyncio.sleep(DELAY_SECONDS)


def memory_changed(ctx: SmsContext, bit_pos, cached_byte, loc_type: str):
    if DEBUG: logger.info(f"memory_changed: {cached_byte}, bit_pos: {bit_pos}")
    bit_list = []

    bit_found = extract_bits(cached_byte, bit_pos)
    bit_list.extend(bit_found)

    # if DEBUG: logger.info("bit_list: " + str(bit_list))
    parse_bits(bit_list, ctx, loc_type)


def send_victory(ctx: SmsContext):
    if ctx.victory:
        return

    ctx.victory = True
    # game_watcher sends the StatusUpdate once ctx.victory is set; send_msgs is a coroutine and
    # calling it here without awaiting only created a never-awaited coroutine.
    logger.info("Congratulations on completing your seed!")
    time.sleep(.05)
    logger.info("ARCHIPELAGO SUPER MARIO SUNSHINE CREDITS:")
    time.sleep(.05)
    logger.info("MrsMarinaRose - Client, Modding and Patching")
    time.sleep(.05)
    logger.info("Hatkirby - APworld")
    time.sleep(.05)
    logger.info("ScorelessPine - Original Manual")
    time.sleep(.05)
    logger.info("Fedora - Logic and testing")
    time.sleep(.05)
    logger.info("J2Slow - Logic and testing")
    time.sleep(.05)
    logger.info("Quizzeh - Extra testing")
    time.sleep(.05)
    # logger.info("DoubleDubbel - The Incredible Name For The Randomizer ISO")
    # time.sleep(.05)
    logger.info("Spicynun - Additional research")
    time.sleep(.05)
    logger.info("JoshuaMKW - Sunshine Toolset")
    time.sleep(.05)
    logger.info("All Archipelago core devs")
    time.sleep(.05)
    logger.info("Nintendo EAD")
    time.sleep(.05)
    logger.info("...and you. Thanks for playing!")
    return


def parse_bits(all_bits, ctx: SmsContext, parse_type: str):
    if DEBUG:
        logger.info("parse_bits: %s", str(all_bits))
    if len(all_bits) == 0:
        return

    for x in all_bits:
        if x != 119 and x <= 911:
            for sms_region in ALL_REGIONS.values():
                possible_locs: list[str] = []
                match parse_type:
                    case "Shine":
                        possible_locs: list[str] = [f"{sms_region.name} - {shine_loc.name}" for shine_loc in
                            sms_region.shines if shine_loc.in_game_bit == x]
                    case "Blue Coin":
                        possible_locs: list[str] = [f"{sms_region.name} - {blue_loc.name}" for blue_loc in
                            sms_region.blue_coins if blue_loc.in_game_bit == x]
                    case "Nozzle":
                        possible_locs: list[str] = [f"{sms_region.name} - {nozz_loc.name}" for nozz_loc in
                            sms_region.nozzle_boxes if nozz_loc.in_game_bit == x]
                    case _:
                        continue

                if not possible_locs:
                    continue

                ctx.locations_checked.add(LOCATION_NAME_TO_ID[possible_locs[0]])
                if DEBUG:
                    logger.info("checks to send: %s", possible_locs[0])
        elif x == 119:
            send_victory(ctx)

def refresh_item_count(ctx, item_id, targ_address):
    counts = ctx.get_item_counts()
    temp = change_endian(counts[item_id])
    #Gravi01 Begin      #Stacktrace where the original Exception was thrown. Keeping the changes in this place as well, you still land here without connection, due to it being an async task
    if ctx.dolphin_status == CONNECTION_CONNECTED_STATUS:
        try:
            dme.write_word(targ_address, temp)
        except Exception:
            logger.info("Connection to Dolphin lost, reconnecting...")
            ctx.dolphin_status = CONNECTION_LOST_STATUS
            dme.un_hook()
    #Gravi01 End


def refresh_all_items(ctx: SmsContext):
    counts = ctx.get_item_counts()
    for item in counts:
        if counts[item] > 0:
            unpack_item(item, ctx)
    if counts[523004] >= ctx.get_corona_goal():
        activate_ticket(999999)
        if not ctx.corona_message_given:
            logger.info("Corona Mountain requirements reached! Reload Delfino Plaza to unlock.")
            ctx.corona_message_given = True


def refresh_collection_counts(ctx):
    #if DEBUG: logger.info("refresh_collection_counts")
    refresh_item_count(ctx, 523004, dme.read_word(addresses.SMS_FLAGS_PTR) + addresses.SHINE_COUNT_OFFSET)
    if ctx.blue_status == 1:
        refresh_item_count(ctx, 523014, dme.read_word(addresses.SMS_FLAGS_PTR) + addresses.BLUECOIN_COUNT_OFFSET)
    refresh_all_items(ctx)


def check_world_flags(byte_location, byte_pos, bool_setting):
    if world_flags.get(byte_location):
        byte_value = world_flags.get(byte_location)
    else:
        byte_value = dme.read_byte(byte_location)
    byte_value = bit_flagger(byte_value, byte_pos, bool_setting)
    world_flags.update({byte_location: byte_value})
    return byte_value


def open_stage(ticket):
    value = check_world_flags(ticket.address, ticket.bit_position, True)
    value |= dme.read_byte(ticket.address)
    dme.write_byte(ticket.address, value)
    return


def special_noki_handling():
    dme.write_byte(addresses.SMS_NOKI_REQ, addresses.SMS_NOKI_LO)
    return


def unpack_item(item, ctx):
    if 522999 < item < 523004:
        activate_nozzle(item, ctx)
    elif item == 523013:
        activate_yoshi(ctx)
    elif 523004 < item < 523012:
        activate_ticket(item)
    elif item == 523140:
        increase_lives(ctx)

@dataclass
class Ticket:
    item_name: str
    item_id: int
    bit_position: int
    course_id: int
    address: int = 0x805789f8
    active: bool = False


TICKETS: list[Ticket] = [
    Ticket("Bianco Hills Ticket", 523005, 5, 2, 0x805789f8),
    Ticket("Ricco Harbor Ticket", 523006, 6, 3, 0x805789f8),
    Ticket("Gelato Beach Ticket", 523007, 7, 4, 0x805789f8),
    Ticket("Pinna Park Ticket", 523008, 1, 5, 0x805789f9),
    Ticket("Noki Bay Ticket", 523009, 3, 9, 0x805789fd),
    Ticket("Sirena Beach Ticket", 523010, 3, 6, 0x805789f9),
    Ticket("Pianta Village Ticket", 523011, 4, 8, 0x805789f9),
    Ticket("Corona Mountain Ticket", 999999, 6, 34, 0x805789fd)
]


def activate_ticket(id: int):
    for tickets in TICKETS:
        if id == tickets.item_id:
            tickets.active = True
            handle_ticket(tickets)
            if not ticket_listing.__contains__(tickets.item_name):
                ticket_listing.append(tickets.item_name)


def handle_ticket(tick: Ticket):
    if not tick.active:
        return
    if tick.item_name == "Noki Bay Ticket":
        special_noki_handling()
    open_stage(tick)
    return

# Not even used
def refresh_all_tickets():
    for tickets in TICKETS:
        handle_ticket(tickets)


def activate_nozzle(id, ctx):
    if id == 523000:
        if not ctx.ap_nozzles_received.__contains__(0):
            ctx.ap_nozzles_received.append(0)
    elif id == 523001:
        if not ctx.ap_nozzles_received.__contains__(1):
            ctx.ap_nozzles_received.append(1)
    elif id == 523002:
        if not ctx.ap_nozzles_received.__contains__(2):
            ctx.ap_nozzles_received.append(2)
        # rocket nozzle
    elif id == 523003:
        if not ctx.ap_nozzles_received.__contains__(3):
            ctx.ap_nozzles_received.append(3)
        # turbo nozzle
    return


def activate_yoshi(ctx):
    dme.write_byte(0x80417A03, 0x01)
    if not ctx.ap_nozzles_received.__contains__(4):
        ctx.ap_nozzles_received.append(4)
    return

# Makes filler 1-UP items actually give lives
# As of now, your life count is increased by 1 if you close the client and reconnect if you already have more than one 1-UP sent
def increase_lives(ctx):
    num_1_ups = ctx.get_item_counts()[523140]
    current_lives = dme.read_word(dme.read_word(addresses.SMS_FLAGS_PTR) + addresses.LIVES_COUNT_OFFSET)

    # Only increase lives a single time when a 1-UP is received
    if ctx.num_1_ups_current != num_1_ups:
        ctx.num_1_ups_current = num_1_ups

        if current_lives < 99 and num_1_ups > 0:
            dme.write_word(dme.read_word(addresses.SMS_FLAGS_PTR) + addresses.LIVES_COUNT_OFFSET, current_lives + 1)
    return

def read_director() -> Optional[int]:
    """Address of the gameplay director, or None while there is none (boot, movies, stage loads)."""
    director = dme.read_word(addresses.MAR_DIRECTOR_PTR)
    if not MEM1_START <= director < MEM1_END - DIRECTOR_SIZE:
        return None
    # The pointer can be left over from a director that was freed; only trust a live gameplay one.
    if dme.read_word(director) != addresses.MAR_DIRECTOR_VTABLE:
        return None
    return director


def server_connected(ctx: SmsContext) -> bool:
    return (ctx.slot is not None and ctx.server is not None and ctx.server.socket is not None
            and not ctx.server.socket.closed)


async def send_mario_death(ctx: SmsContext) -> None:
    """Send Mario's death, or keep it (unsent_death) until the AP connection is back."""
    if not server_connected(ctx):
        return
    player_name = ctx.player_names[ctx.slot] if ctx.slot in ctx.player_names else "Player"
    try:
        await ctx.send_death(f"{player_name} died!")
    except websockets.exceptions.ConnectionClosed:
        return
    ctx.unsent_death = False
    logger.info(f"Sent DeathLink: Mario died")


def kill_mario(director: int) -> bool:
    """Kill Mario the way the game does for a normal death: set the director's game-over flag,
    which the game acts on as soon as Mario is in normal play (after a talk or cutscene ends).
    Only called while the director is in normal play. Returns whether the flag is set."""
    # The title / file select screen also runs a director (stage 15), and during a stage change the
    # pointer can still be the old stage's. Wait until the player is in a stage, so it lands there.
    current_stage = dme.read_byte(addresses.SMS_CURRENT_STAGE)
    if current_stage == FILE_SELECT_STAGE or current_stage != dme.read_byte(addresses.SMS_NEXT_STAGE):
        return False

    flags = int.from_bytes(dme.read_bytes(director + addresses.DIRECTOR_FLAGS_OFFSET, 2), byteorder="big")
    if flags & DIRECTOR_FLAG_GAME_OVER_PENDING:
        return True
    if flags & ~DIRECTOR_IDLE_FLAGS:
        # Another event is pending (shine get, stage change, ...). A death would take priority over
        # it and e.g. lose the shine, so let it finish and try again on a later tick.
        return False

    # Only write the low byte, so the per-frame bits in the high byte are left alone. Re-read it
    # first to narrow the window in which the game sets another event we would overwrite.
    low_byte_address = director + addresses.DIRECTOR_FLAGS_OFFSET + 1
    if dme.read_byte(low_byte_address) != flags & 0xFF:
        return False
    dme.write_byte(low_byte_address, (flags | DIRECTOR_FLAG_GAME_OVER_PENDING) & 0xFF)
    return True


async def resolve_tickets(stage, ctx):
    for tick in TICKETS:
        if tick.course_id == stage and not tick.active:
            logger.info("Entering a stage without a ticket! Initiating bootout...")
            # Byte 1 should correspond to Delfino Plaza
            dme.write_byte(addresses.SMS_NEXT_STAGE, 1)
            dme.write_byte(addresses.SMS_CURRENT_STAGE, 1)
            await send_map_id(1, ctx)
            return
    # Send the map id once, not once per non-matching ticket.
    await send_map_id(stage, ctx)
    return

# Checks to see if player changed stages to update map_id for Poptracker
async def send_map_id(map_id, ctx):
    if map_id == ctx.last_map_id:
        return
    ctx.last_map_id = map_id
    await ctx.send_msgs([{
        "cmd": "Set",
        "key": f"sms_map_{ctx.team}_{ctx.slot}",
        "default": 0,
        "want_reply": False,
        "operations": [{"operation": "replace", "value": map_id}]
    }])

async def send_episode_id(episode_id, ctx):
    if episode_id == ctx.last_episode_id:
        return
    ctx.last_episode_id = episode_id
    await ctx.send_msgs([{
        "cmd": "Set",
        "key": f"sms_episode_{ctx.team}_{ctx.slot}",
        "default": 0,
        "want_reply": False,
        "operations": [{"operation": "replace", "value": episode_id}]
    }])


def main(*launch_args: str):
    import colorama
    from .iso_helper.sms_rom import SMSPatch

    server_address: str = ""
    rom_path: str = ""

    parser = get_base_parser()
    parser.add_argument("apsms_file", default="", type=str, nargs="?", help="Path to a APSMS File")
    args = parser.parse_args(launch_args)

    if args.apsms_file:
        sms_patch = SMSPatch()
        try:
            sms_manifest = sms_patch.read_contents(args.apsms_file)
            server_address = sms_manifest["server"]
            rom_path = sms_patch.patch(args.apsms_file)
        except Exception as ex:
            logger.error("Unable to patch your Super Mario Sunshine. Additional Details:\n" + str(ex))
            Utils.messagebox("Cannot Patch Super Mario Sunshine", "Unable to patch your Super Mario Sunshine ROM as " +
                "expected. Additional details:\n" + str(ex), True)
            raise ex

    async def _main(connect, password):
        ctx = SmsContext(server_address if server_address else connect, password)
        ctx.server_task = asyncio.create_task(server_loop(ctx), name="ServerLoop")

        # ctx._main()

        if TRACKER_LOADED:
            ctx.run_generator()
        if gui_enabled:
            ctx.run_gui()
        ctx.run_cli()
        await asyncio.sleep(1)

        game_start()

        ctx.dolphin_sync_task = asyncio.create_task(dolphin_sync_task(ctx), name="SmsDolphinSync")

        progression_watcher = asyncio.create_task(game_watcher(ctx), name="SmsProgressionWatcher")
        arbitrary = asyncio.create_task(arbitrary_ram_checks(ctx), name="SmsArbitraryWatcher")

        await ctx.exit_event.wait()
        ctx.server_address = None

        await ctx.shutdown()

        if ctx.dolphin_sync_task:
            await ctx.dolphin_sync_task

        if progression_watcher:
            await progression_watcher

        if arbitrary:
            await arbitrary

    colorama.just_fix_windows_console()
    asyncio.run(_main(args.connect, args.password))
    colorama.deinit()


if __name__ == "__main__":
    Utils.init_logging("SMSClient", exception_logger="Client")
    main(*sys.argv[1:])
