package dev.wanderlust.restart;

import com.mojang.brigadier.builder.LiteralArgumentBuilder;
import net.minecraft.ChatFormatting;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.neoforge.event.RegisterCommandsEvent;

public final class RestartCommands {

    private RestartCommands() {}

    @SubscribeEvent
    public static void onRegisterCommands(RegisterCommandsEvent event) {
        // Кнопки в чате дёргают именно её. Доступна всем.
        event.getDispatcher().register(
                Commands.literal("restartvote")
                        .then(Commands.literal("yes").executes(ctx -> vote(ctx.getSource(), true)))
                        .then(Commands.literal("no").executes(ctx -> vote(ctx.getSource(), false)))
        );

        LiteralArgumentBuilder<CommandSourceStack> root = Commands.literal("wlrestart");

        root.executes(ctx -> status(ctx.getSource()));
        root.then(Commands.literal("status").executes(ctx -> status(ctx.getSource())));

        root.then(Commands.literal("now")
                .requires(s -> s.hasPermission(3))
                .executes(ctx -> {
                    ctx.getSource().sendSuccess(() -> Component.literal("Перезапускаю сервер."), true);
                    RestartManager.doRestart();
                    return 1;
                }));

        root.then(Commands.literal("postpone")
                .requires(s -> s.hasPermission(3))
                .executes(ctx -> {
                    RestartManager.postpone();
                    ctx.getSource().sendSuccess(
                            () -> Component.literal("Перезапуск перенесён. " + RestartManager.statusLine()), true);
                    return 1;
                }));

        root.then(Commands.literal("reload")
                .requires(s -> s.hasPermission(3))
                .executes(ctx -> {
                    RestartManager.reload();
                    ctx.getSource().sendSuccess(
                            () -> Component.literal("Конфиг перечитан. " + RestartManager.statusLine()), true);
                    return 1;
                }));

        event.getDispatcher().register(root);
    }

    private static int vote(CommandSourceStack source, boolean forRestart) {
        ServerPlayer player = source.getPlayer();
        if (player == null) {
            source.sendFailure(Component.literal("Голосовать может только игрок."));
            return 0;
        }
        if (!RestartManager.castVote(player, forRestart)) {
            source.sendFailure(Component.literal("Голосование сейчас не идёт."));
            return 0;
        }
        return 1;
    }

    private static int status(CommandSourceStack source) {
        source.sendSuccess(() -> Component.literal(RestartManager.statusLine())
                .withStyle(ChatFormatting.GRAY), false);
        return 1;
    }
}
