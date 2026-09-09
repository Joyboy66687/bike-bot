"""User-facing text catalogue.

The legacy handler module still owns a few flow-specific messages; this
catalogue is the stable home for shared translations used by new handlers.
"""
TEXTS = {
    "ru": {
        "wallet": "🎒 *Мой кошелёк*\n\n💰 Твой текущий баланс: *{balance} zł*",
    },
    "uk": {
        "wallet": "🎒 *Мій гаманець*\n\n💰 Твій поточний баланс: *{balance} zł*",
    },
}
