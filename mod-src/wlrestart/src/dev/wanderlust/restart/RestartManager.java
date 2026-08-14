package dev.wanderlust.restart;

import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.HoverEvent;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.network.protocol.game.ClientboundSetSubtitleTextPacket;
import net.minecraft.network.protocol.game.ClientboundSetTitleTextPacket;
import net.minecraft.network.protocol.game.ClientboundSetTitlesAnimationPacket;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.sounds.SoundEvents;
import net.minecraft.sounds.SoundSource;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.loading.FMLPaths;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.event.server.ServerStoppedEvent;
import net.neoforged.neoforge.event.server.ServerStoppingEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;

import java.nio.file.Path;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * Считает время до перезапуска, проводит голосование и в итоге гасит сервер.
 *
 * Сервер именно ГАСИТСЯ (halt) — поднять его обратно должна панель хостинга
 * или скрипт-обёртка. Без автозапуска снаружи мод просто выключит сервер.
 */
public final class RestartManager {

    private static final Path CONFIG_FILE = FMLPaths.CONFIGDIR.get().resolve("wlrestart.json");

    /** С какой секунды начинается обратный отсчёт титрами. Дальше — каждую секунду до 1. */
    private static final int COUNTDOWN_FROM = 10;

    private static MinecraftServer server;
    private static RestartConfig cfg = new RestartConfig();

    private static long restartAtMs;
    private static boolean voteOpen;
    private static boolean restarting;
    /** Надо ли при полной остановке выйти с ненулевым кодом (см. RestartConfig.exitCode). */
    private static boolean exitRequested;

    /** uuid -> true = за перезапуск, false = против. */
    private static final Map<UUID, Boolean> votes = new HashMap<>();
    /** Какие ранние предупреждения уже показали в этом цикле. */
    private static final Set<Integer> announced = new HashSet<>();
    /** Какие минутные напоминания голосования уже показали. */
    private static final Set<Integer> voteReminders = new HashSet<>();
    /** Какие секунды финального отсчёта уже показали. */
    private static final Set<Integer> countdownShown = new HashSet<>();

    private static long lastCheckMs;

    private RestartManager() {}

    // ------------------------------------------------------------------ жизненный цикл

    @SubscribeEvent
    public static void onServerStarted(ServerStartedEvent event) {
        server = event.getServer();
        cfg = RestartConfig.load(CONFIG_FILE);
        scheduleNext(cfg.intervalHours * 3600.0D);
        WlRestart.LOGGER.info("[wlrestart] автоперезапуск {}, интервал {} ч, голосование за {} мин, порог отмены {}%",
                cfg.enabled ? "включён" : "выключен", cfg.intervalHours, cfg.voteMinutes, cfg.vetoPercent);
    }

    @SubscribeEvent
    public static void onServerStopping(ServerStoppingEvent event) {
        exitRequested = restarting && cfg.exitCode != 0;
        server = null;
        restarting = false;
        resetCycle();
    }

    /**
     * Сервер уже полностью остановлен и мир записан — можно безопасно выйти
     * с нужным кодом, если панель хостинга поднимает сервер только «после падения».
     */
    @SubscribeEvent
    public static void onServerStopped(ServerStoppedEvent event) {
        if (!exitRequested) return;
        WlRestart.LOGGER.info("[wlrestart] выхожу с кодом {} — панель должна поднять сервер обратно", cfg.exitCode);
        Runtime.getRuntime().halt(cfg.exitCode);
    }

    @SubscribeEvent
    public static void onTick(ServerTickEvent.Post event) {
        if (server == null || restarting || !cfg.enabled) return;

        long now = System.currentTimeMillis();
        if (now - lastCheckMs < 250L) return;   // хватит четырёх проверок в секунду
        lastCheckMs = now;

        long leftSec = Math.max(0L, (restartAtMs - now + 999L) / 1000L);

        if (!voteOpen) {
            for (int m : cfg.announceMinutes) {
                if (m * 60L > cfg.voteMinutes * 60L && leftSec <= m * 60L && announced.add(m)) {
                    broadcast(Component.literal("Плановый перезапуск через " + m + " мин.")
                            .withStyle(ChatFormatting.GRAY));
                }
            }
            if (leftSec <= cfg.voteMinutes * 60L) openVote();
        } else {
            tickVote(leftSec);
        }

        if (leftSec <= 0L) finish();
    }

    // ------------------------------------------------------------------ голосование

    private static void openVote() {
        List<ServerPlayer> players = server.getPlayerList().getPlayers();
        if (players.isEmpty() && cfg.skipVoteIfEmpty) {
            WlRestart.LOGGER.info("[wlrestart] на сервере никого — перезапускаюсь без голосования");
            doRestart();
            return;
        }

        voteOpen = true;
        votes.clear();
        voteReminders.clear();
        countdownShown.clear();

        broadcast(line());
        broadcast(Component.literal("Плановый перезапуск через " + cfg.voteMinutes + " мин.")
                .withStyle(ChatFormatting.YELLOW, ChatFormatting.BOLD));
        broadcast(Component.literal("Если против будет хотя бы " + cfg.vetoPercent
                        + "% онлайна — перезапуск отменится на " + cfg.postponeMinutes + " мин.")
                .withStyle(ChatFormatting.GRAY));
        broadcast(buttons());
        broadcast(tally());
        broadcast(line());
    }

    private static void tickVote(long leftSec) {
        if (leftSec > COUNTDOWN_FROM) {
            int min = (int) (leftSec / 60L);
            if (leftSec % 60L <= 1L && min > 0 && voteReminders.add(min)) {
                broadcast(Component.literal("До перезапуска " + min + " мин. ")
                        .withStyle(ChatFormatting.YELLOW)
                        .append(tallyInline()));
                broadcast(buttons());
            }
            return;
        }

        int sec = (int) leftSec;
        if (sec >= 1 && sec <= COUNTDOWN_FROM && countdownShown.add(sec)) {
            // Крупная цифра на весь экран, под ней — что происходит.
            title(Component.literal(Integer.toString(sec))
                            .withStyle(ChatFormatting.RED, ChatFormatting.BOLD),
                    Component.literal("Перезапуск").withStyle(ChatFormatting.YELLOW),
                    0, 20, 8);
            // Тон растёт к нулю: 10 сек — низкий, 1 сек — почти самый высокий.
            float pitch = 0.7F + (COUNTDOWN_FROM - sec) * 0.13F;
            sound(SoundEvents.NOTE_BLOCK_PLING.value(), 1.0F, pitch);
        }
    }

    /** Голос игрока. Переголосовать можно сколько угодно раз до конца отсчёта. */
    public static boolean castVote(ServerPlayer player, boolean forRestart) {
        if (!voteOpen) return false;
        votes.put(player.getUUID(), forRestart);
        player.sendSystemMessage(Component.literal("Твой голос учтён: ")
                .withStyle(ChatFormatting.GRAY)
                .append(forRestart
                        ? Component.literal("за перезапуск").withStyle(ChatFormatting.GREEN)
                        : Component.literal("против перезапуска").withStyle(ChatFormatting.RED)));
        actionBar(tallyInline());
        return true;
    }

    private static void finish() {
        int online = server.getPlayerList().getPlayerCount();
        int against = countAgainst();

        // Порог считаем от числа игроков онлайн, а не от числа проголосовавших:
        // молчание — это не голос против.
        boolean vetoed = online > 0 && against * 100 >= cfg.vetoPercent * online;

        voteOpen = false;

        if (vetoed) {
            broadcast(line());
            broadcast(Component.literal("Перезапуск отменён — против " + against + " из " + online + ".")
                    .withStyle(ChatFormatting.GREEN, ChatFormatting.BOLD));
            broadcast(Component.literal("Следующая попытка через " + cfg.postponeMinutes + " мин.")
                    .withStyle(ChatFormatting.GRAY));
            broadcast(line());
            WlRestart.LOGGER.info("[wlrestart] перезапуск отклонён ({} против из {} онлайн), перенос на {} мин",
                    against, online, cfg.postponeMinutes);
            scheduleNext(cfg.postponeMinutes * 60.0D);
            return;
        }

        WlRestart.LOGGER.info("[wlrestart] перезапуск подтверждён ({} против из {} онлайн)", against, online);
        doRestart();
    }

    private static int countAgainst() {
        int n = 0;
        for (Boolean v : votes.values()) if (!v) n++;
        return n;
    }

    // ------------------------------------------------------------------ собственно перезапуск

    public static void doRestart() {
        if (restarting || server == null) return;
        restarting = true;
        voteOpen = false;

        broadcast(Component.literal("Сервер перезапускается…").withStyle(ChatFormatting.RED, ChatFormatting.BOLD));
        title(Component.literal("ПЕРЕЗАПУСК").withStyle(ChatFormatting.RED, ChatFormatting.BOLD),
                Component.literal("Заходи обратно через минуту").withStyle(ChatFormatting.GRAY),
                0, 120, 20);
        sound(SoundEvents.NOTE_BLOCK_BELL.value(), 1.0F, 1.0F);
        WlRestart.LOGGER.info("[wlrestart] сохраняю мир и гашу сервер");

        MinecraftServer s = server;
        s.execute(() -> {
            try {
                s.saveEverything(false, true, true);
            } catch (Exception e) {
                WlRestart.LOGGER.error("[wlrestart] сохранение перед перезапуском не прошло", e);
            }
            Component kick = Component.literal(cfg.kickMessage);
            for (ServerPlayer p : List.copyOf(s.getPlayerList().getPlayers())) {
                p.connection.disconnect(kick);
            }
            s.halt(false);
        });
    }

    // ------------------------------------------------------------------ управление снаружи

    public static void reload() {
        cfg = RestartConfig.load(CONFIG_FILE);
        resetCycle();
        scheduleNext(cfg.intervalHours * 3600.0D);
    }

    /** Отменить текущий цикл вручную и перенести на postponeMinutes. */
    public static void postpone() {
        voteOpen = false;
        resetCycle();
        scheduleNext(cfg.postponeMinutes * 60.0D);
    }

    private static void scheduleNext(double seconds) {
        restartAtMs = System.currentTimeMillis() + (long) (seconds * 1000.0D);
        resetCycle();
    }

    private static void resetCycle() {
        votes.clear();
        announced.clear();
        voteReminders.clear();
        countdownShown.clear();
    }

    public static long secondsLeft() {
        return Math.max(0L, (restartAtMs - System.currentTimeMillis() + 999L) / 1000L);
    }

    public static boolean isVoteOpen() {
        return voteOpen;
    }

    public static boolean isEnabled() {
        return cfg.enabled;
    }

    public static String statusLine() {
        if (!cfg.enabled) return "автоперезапуск выключен в конфиге";
        long left = secondsLeft();
        String time = formatLeft(left);
        if (voteOpen) {
            int online = server == null ? 0 : server.getPlayerList().getPlayerCount();
            return "голосование идёт, до перезапуска " + time
                    + " (против " + countAgainst() + " из " + online + ")";
        }
        return "до перезапуска " + time;
    }

    private static String formatLeft(long sec) {
        long h = sec / 3600L, m = (sec % 3600L) / 60L, s = sec % 60L;
        if (h > 0) return h + " ч " + m + " мин";
        if (m > 0) return m + " мин " + s + " сек";
        return s + " сек";
    }

    // ------------------------------------------------------------------ текст

    private static MutableComponent line() {
        return Component.literal("─".repeat(46)).withStyle(ChatFormatting.DARK_GRAY);
    }

    private static MutableComponent buttons() {
        MutableComponent yes = Component.literal(" [ ЗА ] ")
                .withStyle(s -> s.withColor(ChatFormatting.GREEN).withBold(true)
                        .withClickEvent(new ClickEvent(ClickEvent.Action.RUN_COMMAND, "/restartvote yes"))
                        .withHoverEvent(new HoverEvent(HoverEvent.Action.SHOW_TEXT,
                                Component.literal("Перезапустить сервер"))));

        MutableComponent no = Component.literal(" [ ПРОТИВ ] ")
                .withStyle(s -> s.withColor(ChatFormatting.RED).withBold(true)
                        .withClickEvent(new ClickEvent(ClickEvent.Action.RUN_COMMAND, "/restartvote no"))
                        .withHoverEvent(new HoverEvent(HoverEvent.Action.SHOW_TEXT,
                                Component.literal("Отложить перезапуск"))));

        return Component.literal("   ").append(yes).append(Component.literal("  ")).append(no);
    }

    private static MutableComponent tally() {
        return Component.literal("   ").append(tallyInline());
    }

    private static MutableComponent tallyInline() {
        int online = server == null ? 0 : server.getPlayerList().getPlayerCount();
        int against = countAgainst();
        int forR = votes.size() - against;
        String text = "за " + forR + " · против " + against + " из " + online;
        if (online > 0) {
            // Сколько голосов против ещё нужно, чтобы порог был взят.
            int need = (int) Math.ceil(cfg.vetoPercent * online / 100.0D);
            text += need > against
                    ? " (ещё " + (need - against) + " — и перезапуска не будет)"
                    : " (порог взят, перезапуск отменится)";
        }
        return Component.literal(text).withStyle(ChatFormatting.GRAY);
    }

    private static void broadcast(Component c) {
        if (server != null) server.getPlayerList().broadcastSystemMessage(c, false);
    }

    private static void actionBar(Component c) {
        if (server == null) return;
        for (ServerPlayer p : server.getPlayerList().getPlayers()) {
            p.sendSystemMessage(c, true);
        }
    }

    /** Титры на весь экран. Времена в тиках: появление, показ, угасание. */
    private static void title(Component main, Component sub, int fadeIn, int stay, int fadeOut) {
        if (server == null) return;
        ClientboundSetTitlesAnimationPacket times = new ClientboundSetTitlesAnimationPacket(fadeIn, stay, fadeOut);
        ClientboundSetSubtitleTextPacket subtitle = new ClientboundSetSubtitleTextPacket(sub);
        ClientboundSetTitleTextPacket text = new ClientboundSetTitleTextPacket(main);
        for (ServerPlayer p : server.getPlayerList().getPlayers()) {
            p.connection.send(times);
            p.connection.send(subtitle);
            p.connection.send(text);
        }
    }

    /** Звук у каждого игрока, независимо от того, где он стоит. */
    private static void sound(net.minecraft.sounds.SoundEvent event, float volume, float pitch) {
        if (server == null) return;
        for (ServerPlayer p : server.getPlayerList().getPlayers()) {
            p.playNotifySound(event, SoundSource.MASTER, volume, pitch);
        }
    }
}
