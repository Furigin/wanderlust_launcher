package dev.wanderlust.restart;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;

import java.io.IOException;
import java.io.Reader;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Настройки, лежат в config/wlrestart.json. Файл создаётся при первом запуске. */
public class RestartConfig {

    /** Выключает автоперезапуск целиком. Ручной /wlrestart now продолжает работать. */
    public boolean enabled = true;

    /** Через сколько часов после старта сервера перезапускаться. */
    public double intervalHours = 4.0;

    /** За сколько минут до перезапуска открывать голосование. */
    public int voteMinutes = 5;

    /** Какая доля игроков онлайн должна проголосовать против, чтобы перезапуск отменился. */
    public int vetoPercent = 50;

    /** На сколько минут переносится перезапуск, если игроки его отклонили. */
    public int postponeMinutes = 60;

    /** Если на сервере никого нет — перезапускаться сразу, не тратя время на голосование. */
    public boolean skipVoteIfEmpty = true;

    /** За сколько минут до перезапуска писать ранние предупреждения (без кнопок). */
    public int[] announceMinutes = {60, 30, 15};

    /** Что игроки увидят на экране отключения. */
    public String kickMessage = "Плановый перезапуск сервера.\nЗаходи обратно через минуту.";

    // ---------------------------------------------------------------

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

    public static RestartConfig load(Path file) {
        if (Files.exists(file)) {
            try (Reader r = Files.newBufferedReader(file, StandardCharsets.UTF_8)) {
                RestartConfig cfg = GSON.fromJson(r, RestartConfig.class);
                if (cfg != null) {
                    cfg.clamp();
                    return cfg;
                }
                WlRestart.LOGGER.warn("[wlrestart] {} пустой, беру значения по умолчанию", file);
            } catch (Exception e) {
                WlRestart.LOGGER.error("[wlrestart] не смог прочитать {}, беру значения по умолчанию", file, e);
            }
            return new RestartConfig();
        }
        RestartConfig cfg = new RestartConfig();
        cfg.save(file);
        return cfg;
    }

    public void save(Path file) {
        try {
            Files.createDirectories(file.getParent());
            try (Writer w = Files.newBufferedWriter(file, StandardCharsets.UTF_8)) {
                GSON.toJson(this, w);
            }
            WlRestart.LOGGER.info("[wlrestart] записал {}", file);
        } catch (IOException e) {
            WlRestart.LOGGER.error("[wlrestart] не смог записать {}", file, e);
        }
    }

    /** Чинит значения, при которых мод вёл бы себя бессмысленно. */
    private void clamp() {
        if (intervalHours < 0.05D) intervalHours = 0.05D;          // не чаще раза в 3 минуты
        if (voteMinutes < 1) voteMinutes = 1;
        if (voteMinutes * 60.0D > intervalHours * 3600.0D) {        // голосование не длиннее самого цикла
            voteMinutes = Math.max(1, (int) (intervalHours * 60.0D) / 2);
        }
        if (vetoPercent < 1) vetoPercent = 1;
        if (vetoPercent > 100) vetoPercent = 100;
        if (postponeMinutes < 1) postponeMinutes = 1;
        if (announceMinutes == null) announceMinutes = new int[0];
        if (kickMessage == null) kickMessage = "";
    }
}
