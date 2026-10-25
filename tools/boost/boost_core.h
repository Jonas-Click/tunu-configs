#ifndef TUNU_BOOST_CORE_H
#define TUNU_BOOST_CORE_H

#include <stdbool.h>
#include <stdint.h>

enum boost_mode {
    BOOST_DISABLED,
    BOOST_ARMED,
    BOOST_ACTIVE,
    BOOST_WAIT_RELEASE,
    BOOST_COOLDOWN,
    BOOST_INHIBITED
};

enum boost_reason {
    BOOST_REASON_NONE,
    BOOST_REASON_HEARTBEAT,
    BOOST_REASON_SUPPLY,
    BOOST_REASON_MOTOR,
    BOOST_REASON_HEADROOM,
    BOOST_REASON_WRITE
};

struct boost_config {
    float per_pack_amps;
    uint32_t duration_ms;
    uint32_t cooldown_ms;
    uint32_t heartbeat_ms;
    uint32_t supply_ms;
    uint32_t release_ms;
};

struct boost_core {
    struct boost_config config;
    float normal_amps;
    float output_amps;
    uint32_t last_ms;
    uint32_t heartbeat_at;
    uint32_t supply_at;
    uint32_t remaining_ms;
    uint32_t cooldown_left_ms;
    uint32_t release_elapsed_ms;
    uint8_t control;
    uint8_t packs;
    enum boost_mode mode;
    enum boost_reason reason;
    bool configured;
    bool have_heartbeat;
    bool have_supply;
    bool drive;
    bool accepted;
    bool active;
    bool release_required;
};

bool boost_config_valid(const struct boost_config *config, float normal_amps);
bool boost_init(struct boost_core *core, const struct boost_config *config,
                float normal_amps, uint32_t now);
void boost_heartbeat(struct boost_core *core, uint8_t control, bool drive, uint32_t now);
bool boost_supply(struct boost_core *core, uint8_t packs, float normal_amps, uint32_t now);
bool boost_normal(struct boost_core *core, float normal_amps, uint8_t observed_packs);
void boost_step(struct boost_core *core, uint32_t now, bool motor_safe);
void boost_abort(struct boost_core *core);
void boost_status(const struct boost_core *core, uint8_t packet[8]);

#endif
