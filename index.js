const fs = require("fs");
const path = require("path");
const express = require("express");

const {
  Client,
  GatewayIntentBits,
  REST,
  Routes,
  SlashCommandBuilder,
  PermissionFlagsBits,
  ChannelType,
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  EmbedBuilder,
  ModalBuilder,
  TextInputBuilder,
  TextInputStyle
} = require("discord.js");

// ===================== ENV =====================
const PORT = process.env.PORT || 3000;
const DISCORD_TOKEN = process.env.DISCORD_TOKEN;
const CLIENT_ID = process.env.CLIENT_ID;
// Optional: commands appear instantly in that server
const GUILD_ID = process.env.GUILD_ID;

if (!DISCORD_TOKEN || !CLIENT_ID) {
  console.error("Missing env vars: DISCORD_TOKEN and CLIENT_ID are required.");
  process.exit(1);
}

// ===================== Render Web Server =====================
const app = express();
app.get("/", (_, res) => res.status(200).send("WB Spawn Now Bot is running."));
app.listen(PORT, () => console.log(`Web server listening on ${PORT}`));

// ===================== Storage =====================
const STORAGE_FILE = path.join(__dirname, "storage.json");

function loadStore() {
  try {
    return JSON.parse(fs.readFileSync(STORAGE_FILE, "utf8"));
  } catch {
    return { guilds: {} };
  }
}

function saveStore(store) {
  fs.writeFileSync(STORAGE_FILE, JSON.stringify(store, null, 2), "utf8");
}

function ensureGuild(store, guildId) {
  if (!store.guilds[guildId]) {
    store.guilds[guildId] = {
      channelId: null,
      panelMessageId: null,
      bossName: "World Boss",
      pingRoleId: null,
      spawnISO: null,
      notified: false
    };
  }
  return store.guilds[guildId];
}

// ===================== Time Helpers =====================
const SPAWN_INTERVAL_MS = 2 * 60 * 60 * 1000; // 2 hours

function toUnix(date) {
  return Math.floor(date.getTime() / 1000);
}

function fmtTs(date) {
  const unix = toUnix(date);
  return `<t:${unix}:F> (<t:${unix}:R>)`;
}

function msToReadable(ms) {
  if (ms <= 0) return "0m 0s";
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}h ${m}m ${r}s`;
  return `${m}m ${r}s`;
}

/**
 * Spawn time input formats accepted:
 *  1) ISO (recommended): 2026-03-03T20:00:00+08:00
 *  2) YYYY-MM-DD HH:mm  (forced +08:00)
 *  3) HH:mm             (today forced +08:00; based on current UTC date)
 */
function parseSpawnInput(input) {
  const raw = input.trim();

  // (1) ISO or anything Date can parse
  const d1 = new Date(raw);
  if (!Number.isNaN(d1.getTime())) return d1;

  // (2) YYYY-MM-DD HH:mm
  const m1 = raw.match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})$/);
  if (m1) {
    const [_, Y, M, D, hh, mm] = m1;
    return new Date(`${Y}-${M}-${D}T${hh}:${mm}:00+08:00`);
  }

  // (3) HH:mm (today in +08:00)
  const m2 = raw.match(/^(\d{2}):(\d{2})$/);
  if (m2) {
    const utc = new Date();
    const y = utc.getUTCFullYear();
    const mo = String(utc.getUTCMonth() + 1).padStart(2, "0");
    const da = String(utc.getUTCDate()).padStart(2, "0");
    const hh = m2[1];
    const mm = m2[2];
    return new Date(`${y}-${mo}-${da}T${hh}:${mm}:00+08:00`);
  }

  return null;
}

// ===================== UI Builders =====================
function buildPanelEmbed(g) {
  const embed = new EmbedBuilder()
    .setTitle(`🧭 ${g.bossName} Spawn Notifier`)
    .setFooter({ text: "Clean panel • Only sends SPAWN NOW ping" });

  if (!g.spawnISO) {
    embed.setDescription(
      `**Status:** No active timer\n\n` +
      `Press **BOSS DOWN** when the boss dies (spawn = death + 2 hours), or **EDIT TIME** to set spawn manually.\n\n` +
      `**Ping role:** ${g.pingRoleId ? `<@&${g.pingRoleId}>` : "Not set"}`
    );
    return embed;
  }

  const spawn = new Date(g.spawnISO);
  embed.setDescription(
    `**Status:** Timer active\n` +
    `**Schedule spawn time:** ${fmtTs(spawn)}\n` +
    `**Ping role:** ${g.pingRoleId ? `<@&${g.pingRoleId}>` : "Not set"}\n\n` +
    `Use **SHOW TIMER** to see remaining time (ephemeral).`
  );
  return embed;
}

function buildPanelButtons() {
  return [
    new ActionRowBuilder().addComponents(
      new ButtonBuilder().setCustomId("wb_down").setLabel("BOSS DOWN").setStyle(ButtonStyle.Danger),
      new ButtonBuilder().setCustomId("wb_edit").setLabel("EDIT TIME").setStyle(ButtonStyle.Secondary),
      new ButtonBuilder().setCustomId("wb_show").setLabel("SHOW TIMER").setStyle(ButtonStyle.Primary),
      new ButtonBuilder().setCustomId("wb_reset").setLabel("RESET").setStyle(ButtonStyle.Secondary)
    )
  ];
}

async function upsertPanel(guild, g) {
  const channel = guild.channels.cache.get(g.channelId);
  if (!channel || !channel.isTextBased()) return;

  const payload = { embeds: [buildPanelEmbed(g)], components: buildPanelButtons() };

  if (g.panelMessageId) {
    try {
      const msg = await channel.messages.fetch(g.panelMessageId);
      await msg.edit(payload);
      return;
    } catch {
      g.panelMessageId = null;
    }
  }

  const msg = await channel.send(payload);
  g.panelMessageId = msg.id;
}

// ===================== Bot Logic =====================
function setSpawnFromNow(g) {
  const now = new Date();
  const spawn = new Date(now.getTime() + SPAWN_INTERVAL_MS);
  g.spawnISO = spawn.toISOString();
  g.notified = false;
}

function setSpawnManual(g, dateObj) {
  g.spawnISO = dateObj.toISOString();
  g.notified = false;
}

function resetTimer(g) {
  g.spawnISO = null;
  g.notified = false;
}

// ===================== Discord Client =====================
const client = new Client({ intents: [GatewayIntentBits.Guilds] });

// Slash commands
const commands = [
  new SlashCommandBuilder()
    .setName("panel")
    .setDescription("Post the World Boss control panel")
    .addChannelOption(o =>
      o.setName("channel")
        .setDescription("Channel where panel + spawn ping will be sent")
        .addChannelTypes(ChannelType.GuildText)
        .setRequired(true)
    )
    .addRoleOption(o =>
      o.setName("ping_role")
        .setDescription("Role to ping on SPAWN NOW")
        .setRequired(true)
    )
    .addStringOption(o =>
      o.setName("boss_name")
        .setDescription("Optional display name (default: World Boss)")
        .setRequired(false)
    )
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild),

  new SlashCommandBuilder()
    .setName("setrole")
    .setDescription("Change the role pinged on SPAWN NOW")
    .addRoleOption(o =>
      o.setName("ping_role")
        .setDescription("Role to ping")
        .setRequired(true)
    )
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
].map(c => c.toJSON());

async function registerCommands() {
  const rest = new REST({ version: "10" }).setToken(DISCORD_TOKEN);

  if (GUILD_ID) {
    await rest.put(Routes.applicationGuildCommands(CLIENT_ID, GUILD_ID), { body: commands });
    console.log("Registered GUILD slash commands.");
  } else {
    await rest.put(Routes.applicationCommands(CLIENT_ID), { body: commands });
    console.log("Registered GLOBAL slash commands (can take time to appear).");
  }
}

// ===================== Interactions =====================
client.on("interactionCreate", async (interaction) => {
  const store = loadStore();

  // Slash commands
  if (interaction.isChatInputCommand()) {
    const guildId = interaction.guildId;
    if (!guildId) return;
    const g = ensureGuild(store, guildId);

    try {
      if (interaction.commandName === "panel") {
        const channel = interaction.options.getChannel("channel", true);
        const role = interaction.options.getRole("ping_role", true);
        const bossName = interaction.options.getString("boss_name", false);

        g.channelId = channel.id;
        g.pingRoleId = role.id;
        if (bossName && bossName.trim()) g.bossName = bossName.trim();

        saveStore(store);

        await upsertPanel(interaction.guild, g);
        saveStore(store);

        await interaction.reply({ content: `✅ Panel posted in ${channel}.`, ephemeral: true });
        return;
      }

      if (interaction.commandName === "setrole") {
        const role = interaction.options.getRole("ping_role", true);
        g.pingRoleId = role.id;
        saveStore(store);

        await upsertPanel(interaction.guild, g);
        saveStore(store);

        await interaction.reply({ content: `✅ Ping role set to ${role}.`, ephemeral: true });
        return;
      }
    } catch (e) {
      console.error(e);
      await interaction.reply({ content: "❌ Error running command.", ephemeral: true }).catch(() => {});
    }
    return;
  }

  // Buttons
  if (interaction.isButton()) {
    const guildId = interaction.guildId;
    if (!guildId) return;
    const g = ensureGuild(store, guildId);

    if (!g.channelId) {
      await interaction.reply({ content: "⚠️ Run `/panel` first.", ephemeral: true });
      return;
    }

    try {
      if (interaction.customId === "wb_down") {
        setSpawnFromNow(g);
        saveStore(store);

        await upsertPanel(interaction.guild, g);
        saveStore(store);

        await interaction.reply({
          content: `✅ Timer started.\nSchedule spawn time: ${fmtTs(new Date(g.spawnISO))}`,
          ephemeral: true
        });
        return;
      }

      if (interaction.customId === "wb_show") {
        if (!g.spawnISO) {
          await interaction.reply({ content: "No active timer. Press **BOSS DOWN**.", ephemeral: true });
          return;
        }

        const spawn = new Date(g.spawnISO);
        const leftMs = spawn.getTime() - Date.now();

        await interaction.reply({
          content:
            `⏳ **${g.bossName}**\n` +
            `Schedule spawn time: ${fmtTs(spawn)}\n` +
            `Time left: **${msToReadable(leftMs)}**`,
          ephemeral: true
        });
        return;
      }

      if (interaction.customId === "wb_reset") {
        resetTimer(g);
        saveStore(store);

        await upsertPanel(interaction.guild, g);
        saveStore(store);

        await interaction.reply({ content: "✅ Timer reset.", ephemeral: true });
        return;
      }

      if (interaction.customId === "wb_edit") {
        const modal = new ModalBuilder()
          .setCustomId("wb_edit_modal")
          .setTitle("Edit Spawn Time");

        const input = new TextInputBuilder()
          .setCustomId("spawn_time")
          .setLabel("Spawn time (ISO recommended)")
          .setStyle(TextInputStyle.Short)
          .setPlaceholder("2026-03-03T20:00:00+08:00  OR  2026-03-03 20:00  OR  20:00")
          .setRequired(true);

        modal.addComponents(new ActionRowBuilder().addComponents(input));
        await interaction.showModal(modal);
        return;
      }
    } catch (e) {
      console.error(e);
      await interaction.reply({ content: "❌ Error handling button.", ephemeral: true }).catch(() => {});
    }
    return;
  }

  // Modal submit for EDIT TIME
  if (interaction.isModalSubmit() && interaction.customId === "wb_edit_modal") {
    const guildId = interaction.guildId;
    if (!guildId) return;
    const g = ensureGuild(store, guildId);

    try {
      const value = interaction.fields.getTextInputValue("spawn_time");
      const parsed = parseSpawnInput(value);

      if (!parsed || Number.isNaN(parsed.getTime())) {
        await interaction.reply({
          content: "❌ Invalid time. Best format: `2026-03-03T20:00:00+08:00`",
          ephemeral: true
        });
        return;
      }

      if (parsed.getTime() < Date.now() - 60 * 1000) {
        await interaction.reply({
          content: "❌ That looks like a past time. Please enter a future spawn time.",
          ephemeral: true
        });
        return;
      }

      setSpawnManual(g, parsed);
      saveStore(store);

      await upsertPanel(interaction.guild, g);
      saveStore(store);

      await interaction.reply({
        content: `✅ Updated.\nSchedule spawn time: ${fmtTs(parsed)}`,
        ephemeral: true
      });
    } catch (e) {
      console.error(e);
      await interaction.reply({ content: "❌ Error saving time.", ephemeral: true }).catch(() => {});
    }
  }
});

// ===================== SPAWN NOW ONLY LOOP =====================
// Checks every 20 seconds; sends only once when time is reached.
async function notifyTick() {
  const store = loadStore();
  const nowMs = Date.now();

  for (const [guildId, g] of Object.entries(store.guilds)) {
    if (!g.channelId || !g.spawnISO) continue;
    if (g.notified) continue;

    const guild = client.guilds.cache.get(guildId);
    if (!guild) continue;

    const channel = guild.channels.cache.get(g.channelId);
    if (!channel || !channel.isTextBased()) continue;

    const spawn = new Date(g.spawnISO);
    if (Number.isNaN(spawn.getTime())) {
      resetTimer(g);
      continue;
    }

    if (nowMs >= spawn.getTime()) {
      g.notified = true;

      const rolePing = g.pingRoleId ? `<@&${g.pingRoleId}>` : "";
      await channel.send(
        `🟥 **WORLD BOSS SPAWN NOW** ${rolePing}\n` +
        `Schedule spawn time: ${fmtTs(spawn)}`
      );

      // Clean reset after notifying
      resetTimer(g);

      // Update panel to "No active timer"
      try {
        await upsertPanel(guild, g);
      } catch {}
    }
  }

  saveStore(store);
}

client.once("ready", async () => {
  console.log(`Logged in as ${client.user.tag}`);

  await registerCommands().catch(console.error);

  setInterval(() => notifyTick().catch(console.error), 20 * 1000);
  notifyTick().catch(console.error);
});

client.login(DISCORD_TOKEN);
