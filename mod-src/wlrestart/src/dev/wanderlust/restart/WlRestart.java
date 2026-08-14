package dev.wanderlust.restart;

import com.mojang.logging.LogUtils;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.common.NeoForge;
import org.slf4j.Logger;

@Mod(WlRestart.MODID)
public class WlRestart {

    public static final String MODID = "wlrestart";
    public static final Logger LOGGER = LogUtils.getLogger();

    public WlRestart() {
        NeoForge.EVENT_BUS.register(RestartManager.class);
        NeoForge.EVENT_BUS.register(RestartCommands.class);
        LOGGER.info("[wlrestart] загружен");
    }
}
