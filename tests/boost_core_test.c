#include "boost_core.h"

#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

static const struct boost_config config = {85, 1000, 1000, 250, 1000, 100};
static unsigned checks;

static void check(bool condition) {
    ++checks;
    assert(condition);
}

static void inputs(struct boost_core *core, uint32_t now, unsigned control,
                   bool drive, unsigned packs, float normal) {
    boost_step(core, now, true);
    boost_heartbeat(core, control, drive, now);
    check(boost_supply(core, packs, normal, now));
    boost_step(core, now, true);
}

static void advance(struct boost_core *core, uint32_t *now, uint32_t duration,
                    unsigned control, bool drive, unsigned packs, float normal) {
    for (uint32_t elapsed = 0; elapsed < duration; elapsed += 10) {
        *now += 10;
        inputs(core, *now, control, drive, packs, normal);
    }
}

static void arm(struct boost_core *core, uint32_t *now, unsigned packs, float normal) {
    check(boost_init(core, &config, normal, *now));
    inputs(core, *now, 3, false, packs, normal);
    advance(core, now, 1000, 3, false, packs, normal);
    check(core->mode == BOOST_ARMED && core->remaining_ms == 1000);
}

static void expiry_and_rearm(void) {
    struct boost_core core;
    uint32_t now = 0;
    arm(&core, &now, 1, 60);
    inputs(&core, now, 3, true, 1, 60);
    check(core.active && core.output_amps == 85);
    advance(&core, &now, 990, 3, true, 1, 60);
    check(core.active && core.remaining_ms == 10);
    advance(&core, &now, 10, 3, true, 1, 60);
    check(!core.active && core.output_amps == 60 && core.release_required);
    advance(&core, &now, 2000, 3, true, 1, 60);
    check(core.mode == BOOST_WAIT_RELEASE && core.cooldown_left_ms == 0);
    inputs(&core, now, 3, false, 1, 60);
    advance(&core, &now, 100, 3, false, 1, 60);
    check(core.cooldown_left_ms == 1000);
    advance(&core, &now, 1000, 3, true, 1, 60);
    check(!core.active && core.mode == BOOST_WAIT_RELEASE);
    inputs(&core, now, 3, false, 1, 60);
    advance(&core, &now, 100, 3, false, 1, 60);
    inputs(&core, now, 3, true, 1, 60);
    check(core.active);
}

static void release_and_duplicates(void) {
    struct boost_core core;
    uint32_t now = 0;
    arm(&core, &now, 1, 60);
    inputs(&core, now, 3, true, 1, 60);
    advance(&core, &now, 300, 3, true, 1, 60);
    check(core.remaining_ms == 700);
    inputs(&core, now, 1, true, 1, 60);
    advance(&core, &now, 100, 1, true, 1, 60);
    check(!core.active && core.remaining_ms == 700);
    inputs(&core, now, 3, true, 1, 60);
    advance(&core, &now, 700, 3, true, 1, 60);
    check(!core.active && core.remaining_ms == 0);
    inputs(&core, now, 1, false, 1, 60);
    advance(&core, &now, 1100, 3, false, 1, 60);
    inputs(&core, now, 3, true, 1, 60);
    advance(&core, &now, 300, 3, true, 1, 60);
    inputs(&core, now, 3, false, 1, 60);
    advance(&core, &now, 100, 3, false, 1, 60);
    check(!core.active && core.remaining_ms == 1000);
}

static void leases_and_faults(void) {
    struct boost_core core;
    uint32_t now = 0;
    arm(&core, &now, 1, 60);
    inputs(&core, now, 3, true, 1, 60);
    boost_step(&core, now + 249, true);
    check(core.active);
    boost_step(&core, now + 250, true);
    check(!core.active && core.output_amps == 60 && core.release_required);
    inputs(&core, now + 250, 3, true, 1, 60);
    check(!core.active);
    now += 250;
    inputs(&core, now, 3, false, 1, 60);
    advance(&core, &now, 1100, 3, false, 1, 60);
    inputs(&core, now, 3, true, 1, 60);
    boost_step(&core, now + 1, false);
    check(!core.active && !core.accepted && core.output_amps == 60);
    now += 1;
    inputs(&core, now, 3, false, 1, 60);
    advance(&core, &now, 1100, 3, false, 1, 60);
    inputs(&core, now, 3, true, 1, 60);
    for (unsigned i = 0; i < 10; ++i) {
        now += 100;
        boost_step(&core, now, true);
        boost_heartbeat(&core, 3, true, now);
        boost_step(&core, now, true);
    }
    check(!core.active && core.output_amps == 60);
}

static void supplies_and_normal_updates(void) {
    struct boost_core core;
    uint32_t now = 0;
    arm(&core, &now, 3, 120);
    inputs(&core, now, 3, true, 3, 120);
    check(core.output_amps == 170 && core.active);
    check(boost_normal(&core, 80, 1));
    boost_step(&core, now, true);
    check(core.output_amps == 85 && core.packs == 1);
    check(boost_normal(&core, 120, 3));
    boost_step(&core, now, true);
    check(!core.active && core.output_amps == 120 && core.packs == 1);
    check(!boost_supply(&core, 4, 120, now));
    boost_step(&core, now, true);
    check(!core.accepted && core.output_amps == 120);
    check(!boost_normal(&core, INFINITY, 1));
    check(core.output_amps == 120);
}

static void status_and_wrap(void) {
    struct boost_core core;
    uint32_t now = UINT32_MAX - 1500;
    arm(&core, &now, 2, 60);
    inputs(&core, now, 7, true, 2, 60);
    uint8_t packet[8];
    boost_status(&core, packet);
    check(packet[0] == (1 | 4 | 16 | 64));
    check(packet[1] == 0xb1 && packet[3] == 2 && (packet[2] & 16));
    advance(&core, &now, 1000, 7, true, 2, 60);
    check(!core.active && core.output_amps == 60);
    boost_status(&core, packet);
    check(packet[4] == 0 && packet[5] == 0 && !(packet[2] & 16));
    for (unsigned control = 0; control < 8; ++control) {
        inputs(&core, now, control, false, 2, 60);
        boost_status(&core, packet);
        check((packet[0] & 0x30) == ((control & 1) ? 0x10 : 0x20));
        check((packet[0] & 0xc0) == ((control & 4) ? 0x40 : 0x80));
    }
}

static void parameter_validation(void) {
    check(boost_config_valid(&config, 60));
    struct boost_config bad = config;
    bad.per_pack_amps = 85.1f;
    check(!boost_config_valid(&bad, 60));
    bad = config;
    bad.duration_ms = 20001;
    check(!boost_config_valid(&bad, 60));
    bad = config;
    bad.heartbeat_ms = 501;
    check(!boost_config_valid(&bad, 60));
    check(!boost_config_valid(&config, NAN));
    check(!boost_config_valid(&config, 171));
}

static void control_matrix_and_random_interleavings(void) {
    for (unsigned control = 0; control < 8; ++control) {
        for (unsigned packs = 0; packs < 4; ++packs) {
            struct boost_core core;
            uint32_t now = 0;
            check(boost_init(&core, &config, 60, now));
            advance(&core, &now, 1000, control, false, packs, 60);
            inputs(&core, now, control, true, packs, 60);
            unsigned count = (packs & 1) + ((packs >> 1) & 1);
            check(core.active == ((control & 2) && count));
            check(core.output_amps == (core.active ? 85 * count : 60));
        }
    }
    struct boost_core core;
    uint32_t now = UINT32_MAX - 1000;
    uint32_t random = 7;
    check(boost_init(&core, &config, 60, now));
    for (unsigned i = 0; i < 100000; ++i) {
        random = random * 1664525u + 1013904223u;
        now += random % 21;
        bool safe = (random & 31) != 0;
        boost_step(&core, now, safe);
        if (random & 1) {
            boost_heartbeat(&core, (random >> 3) & 7, (random & 4) != 0, now);
        }
        if ((random & 63) == 1) {
            check(boost_supply(&core, (random >> 6) & 3, (random & 512) ? 120 : 60, now));
        }
        boost_step(&core, now, safe);
        unsigned count = (core.packs & 1) + ((core.packs >> 1) & 1);
        check(core.output_amps == (core.active ? 85 * count : core.normal_amps));
        check(core.output_amps <= 170 && core.remaining_ms <= config.duration_ms);
        check(core.cooldown_left_ms <= config.cooldown_ms);
        if (core.active) {
            check(core.accepted && core.drive && safe && core.remaining_ms > 0);
            check(now - core.heartbeat_at < config.heartbeat_ms);
            check(now - core.supply_at < config.supply_ms);
        }
    }
}

int main(void) {
    parameter_validation();
    control_matrix_and_random_interleavings();
    expiry_and_rearm();
    release_and_duplicates();
    leases_and_faults();
    supplies_and_normal_updates();
    status_and_wrap();
    printf("BOOST_CORE_PASS checks=%u\n", checks);
    return 0;
}
