#include "boost_core.h"

#include <math.h>
#include <string.h>

static unsigned pack_count(uint8_t mask) {
    return (mask & 1u) + ((mask >> 1) & 1u);
}

static uint32_t subtract_time(uint32_t value, uint32_t elapsed) {
    return elapsed >= value ? 0 : value - elapsed;
}

bool boost_config_valid(const struct boost_config *config, float normal_amps) {
    return config && isfinite(normal_amps) && normal_amps > 0 && normal_amps <= 170
        && isfinite(config->per_pack_amps) && config->per_pack_amps > 0
        && config->per_pack_amps <= 85
        && config->duration_ms >= 100 && config->duration_ms <= 20000
        && config->cooldown_ms >= 1000 && config->cooldown_ms <= 3600000
        && config->heartbeat_ms >= 20 && config->heartbeat_ms <= 500
        && config->supply_ms >= 100 && config->supply_ms <= 5000
        && config->release_ms >= 20 && config->release_ms <= 1000;
}

bool boost_init(struct boost_core *core, const struct boost_config *config,
                float normal_amps, uint32_t now) {
    if (!boost_config_valid(config, normal_amps)) {
        return false;
    }
    memset(core, 0, sizeof(*core));
    core->config = *config;
    core->normal_amps = normal_amps;
    core->output_amps = normal_amps;
    core->last_ms = now;
    core->control = 1;
    core->configured = true;
    /* A restart cannot be used to refill an exhausted allowance. */
    core->cooldown_left_ms = config->cooldown_ms;
    core->mode = BOOST_COOLDOWN;
    return true;
}

void boost_abort(struct boost_core *core) {
    if (core->active) {
        core->remaining_ms = 0;
        core->release_required = true;
    }
    core->active = false;
    core->accepted = false;
    core->output_amps = core->normal_amps;
    core->mode = BOOST_INHIBITED;
    core->reason = BOOST_REASON_MOTOR;
    core->release_elapsed_ms = 0;
}

void boost_heartbeat(struct boost_core *core, uint8_t control, bool drive, uint32_t now) {
    core->control = control & 7u;
    if (drive != core->drive) {
        core->release_elapsed_ms = 0;
    }
    core->drive = drive;
    core->heartbeat_at = now;
    core->have_heartbeat = true;
}

bool boost_supply(struct boost_core *core, uint8_t packs, float normal_amps, uint32_t now) {
    if (packs > 3 || !isfinite(normal_amps) || normal_amps <= 0 || normal_amps > 170) {
        core->have_supply = false;
        boost_abort(core);
        core->reason = BOOST_REASON_SUPPLY;
        return false;
    }
    core->normal_amps = normal_amps;
    core->packs = packs;
    core->supply_at = now;
    core->have_supply = true;
    return true;
}

bool boost_normal(struct boost_core *core, float normal_amps, uint8_t observed_packs) {
    if (observed_packs > 3 || !isfinite(normal_amps) || normal_amps <= 0 || normal_amps > 170) {
        boost_abort(core);
        return false;
    }
    core->normal_amps = normal_amps;
    /* Ordinary reconciliation can revoke pack evidence, never manufacture it. */
    core->packs &= observed_packs;
    return true;
}

void boost_step(struct boost_core *core, uint32_t now, bool motor_safe) {
    uint32_t elapsed = now - core->last_ms;
    core->last_ms = now;
    bool was_active = core->active;
    if (was_active) {
        core->remaining_ms = subtract_time(core->remaining_ms, elapsed);
        if (!core->remaining_ms) {
            core->release_required = true;
        }
    }
    core->cooldown_left_ms = subtract_time(core->cooldown_left_ms, elapsed);
    core->active = false;
    core->accepted = false;
    core->reason = BOOST_REASON_NONE;
    core->output_amps = core->normal_amps;
    if (!core->configured) {
        core->mode = BOOST_DISABLED;
        return;
    }
    bool heartbeat = core->have_heartbeat
        && now - core->heartbeat_at < core->config.heartbeat_ms;
    bool supply = core->have_supply && core->packs
        && now - core->supply_at < core->config.supply_ms;
    if (heartbeat && motor_safe && !core->drive) {
        uint32_t left = core->config.release_ms - core->release_elapsed_ms;
        core->release_elapsed_ms += elapsed >= left ? left : elapsed;
    } else {
        core->release_elapsed_ms = 0;
    }
    if (!heartbeat || !motor_safe || !supply) {
        if (was_active) {
            core->remaining_ms = 0;
            core->release_required = true;
        }
        core->mode = BOOST_INHIBITED;
        core->reason = !heartbeat ? BOOST_REASON_HEARTBEAT
            : (!motor_safe ? BOOST_REASON_MOTOR : BOOST_REASON_SUPPLY);
        return;
    }
    if (!core->drive && core->release_elapsed_ms >= core->config.release_ms) {
        if (core->release_required) {
            core->release_required = false;
            core->cooldown_left_ms = core->config.cooldown_ms;
        } else if (!core->cooldown_left_ms) {
            core->remaining_ms = core->config.duration_ms;
        }
    }
    float peak = core->config.per_pack_amps * pack_count(core->packs);
    core->accepted = (core->control & 2u) && peak > core->normal_amps;
    if ((core->control & 2u) && peak <= core->normal_amps) {
        core->reason = BOOST_REASON_HEADROOM;
    }
    if (core->cooldown_left_ms) {
        core->mode = BOOST_COOLDOWN;
    } else if (!core->remaining_ms) {
        core->mode = BOOST_WAIT_RELEASE;
    } else if (!core->accepted) {
        core->mode = (core->control & 2u) ? BOOST_INHIBITED : BOOST_DISABLED;
    } else if (core->drive) {
        core->mode = BOOST_ACTIVE;
        core->active = true;
        core->output_amps = peak;
    } else {
        core->mode = BOOST_ARMED;
    }
}

void boost_status(const struct boost_core *core, uint8_t packet[8]) {
    uint8_t flags = (uint8_t)core->mode;
    if (core->accepted) {
        flags |= 8u;
    }
    if (core->active) {
        flags |= 16u;
    }
    if (core->have_heartbeat && core->last_ms - core->heartbeat_at < core->config.heartbeat_ms) {
        flags |= 32u;
    }
    if (core->have_supply && core->last_ms - core->supply_at < core->config.supply_ms) {
        flags |= 64u;
    }
    packet[0] = 1u | (core->accepted ? 4u : 8u)
        | ((core->control & 1u) ? 16u : 32u)
        | ((core->control & 4u) ? 64u : 128u);
    packet[1] = 0xb1;
    packet[2] = flags;
    packet[3] = core->packs | ((uint8_t)core->reason << 2);
    uint32_t remaining = (core->remaining_ms + 99u) / 100u;
    uint32_t cooldown = (core->cooldown_left_ms + 99u) / 100u;
    packet[4] = remaining >> 8;
    packet[5] = remaining;
    packet[6] = cooldown >> 8;
    packet[7] = cooldown;
}
