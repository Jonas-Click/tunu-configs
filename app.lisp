; ===== MIDI imports — must appear before any executable code =====
(import "pkg::midi@://vesc_packages/lib_midi/midi.vescpkg" 'midi)

; Axel F is imported from vesc_tool's built-in stock lib_files package (works
; because vesc_tool resolves :// imports from its OWN resources). It's the
; one song kept special-cased: every OTHER song arrives over CAN from
; music-service immediately before it plays, so app.lisp holds no embedded
; melody content at all — Axel F is a package reference resolved by
; vesc_tool itself, so there's nothing to stream for it. No custom package
; needs to be installed on the device.
(import "pkg::midi_axelf_melody@://vesc_packages/lib_files/files.vescpkg" 'song1-track0)
(import "pkg::midi_axelf_base@://vesc_packages/lib_files/files.vescpkg"   'song1-track1)

(def can-cnt 0)
(def statusByte_1 0x00)
(def tick 0)

; 0x7E8 bytes 4-7: rated power (BCD kW), max speed (BCD km/h), base SW version
; (BCD nibbles), app revision (plain byte).
(def motor-power-kw 0x03)
(def max-speed-kmh 0x45)
(def sw-base-version 0x10)  ; "1.0"
(def sw-app-version 1)

(def pole-pairs 24.0)

; Empirical speed/odometer calibration, matches CalibrationFactor in ecu-service.
(def speed-cal 1.03)

; KERS state
(def kers-enabled false)
(def kers-current 0)
; Regen voltage target in mV (0x4E2, 10 mV units on the wire). Full battery
; regen authority at or below it, tapering linearly to zero across the band
; above it, so braking into a nearly full pack eases off instead of pushing it
; over the target.
(def kers-voltage 56000)
(def kers-voltage-taper 3000.0)
; Battery-side regen limit currently applied (A, negative), so the loop only
; writes the config when it actually changes.
(def kers-amps-now 0.0)

; Low-speed KERS fade, in ERPM so it tracks the FOC sensor handover directly.
; Coming down, the observer hands back to the halls between foc_sl_erpm (2000)
; and foc_sl_erpm_start (1800). Braking through that blend at full current is
; what rattles, so the fade bottoms out at a floor just above the blend and the
; floor carries through it, keeping the lever authoritative at crawl. The fade
; band sits at very low speed: full regen down to ~8.5 km/h, floor below that.
; Below the standstill cutoff the halls only give a coarse sector angle, and
; brake current there can turn into a slight forward pull, so the scale drops
; to zero — KERS on or off. A forward drive request bypasses both the cutoff
; and fade: VESC also applies this brake-side limit to forward torque while
; moving backwards.
; With 48 poles and the 0.416 m wheel: 2200 erpm ~ 7.2 km/h, 2600 ~ 8.5, 300 ~ 1.
(def kers-fade-start-erpm 2600.0)
(def kers-fade-end-erpm 2200.0)
(def kers-floor 0.25)
(def kers-hold-cutoff-erpm 300.0)
; Net throttle-minus-brake level, in ADC units, above which the rider counts as
; asking for drive. The ADC app zeroes anything below its own 0.15 deadband, so
; this is already a real command.
(def kers-drive-request-level 0.15)
; Battery charging allowance for a forward drive command, independent of KERS.
; Opposing backward motion can regenerate. The voltage taper and firmware
; battery/BMS limits still apply.
(def kers-drive-regen-floor -3.0)
; Max change in scale per 5 ms tick: engage ramps in fast but not as a step
; (~60 ms full sweep), and a noisy get-rpm in the fade band cannot chatter the
; limit. Forward drive uses the ADC app's torque ramp, not this brake fade.
(def kers-scale-slew 0.08)
; Is the rider asking for drive? The ADC app applies its own 0.15 deadband to
; the same net value before it issues any current, so this is the earliest
; honest signal that a drive command is on its way.
(defun drive-requested ()
    (> (- (get-adc-decoded 0) (get-adc-decoded 1)) kers-drive-request-level)
)

(def kers-scale 0.0)

; Shutdown detection: ADC channel 2 sits at ~2.63V while powered and drops on
; power loss. (Channel 3 reads ~2.23V on this unit, already below the
; threshold, so it fired right at boot instead.)
(def shutdown-threshold 2.48)
(def shutdown-triggered false)

; ===== Music: streamed song buffers =====
; music-service streams a song's decoded MIDI bytes over CAN
; (Music_Upload_Begin/Chunk/End, below) immediately before playing it, into
; two FIXED buffers that are only ever refilled, never recreated.
(def song-buf-max 2048)   ; headroom over the largest known track
(def song-buf-0-len 0)
(def song-buf-1-len 0)
(def song-buf-0-vol 1.0)
(def song-buf-1-vol 1.0)
(def song-buf-0-decay 0.92)
(def song-buf-1-decay 0.92)
(def song-buf-0-ready false)
(def song-buf-1-ready false)
(def resident-song-number 0)   ; number of the song currently loaded, 0 = none

; Upload-in-progress state. One track uploads at a time; Begin resets this.
(def upload-active-slot -1)    ; 0 or 1 mid-upload, -1 when idle
(def upload-checksum 0)        ; running sum-of-bytes mod 256
(def upload-received 0)        ; bytes received so far for the active upload

; Axel F's reserved song number — must match songs.json's "number" for the
; "Axel F" / "source": "builtin-package" entry on the music-service side.
(def axelf-song-number 1)

; ===== Music control CAN IDs =====
; All in the 0x4E0-0x4EF range that the VESC firmware filter passes through
; to LispBM. ecu-service only uses 0x4E0/0x4E1/0x4E2/0x4EB/0x4EF, so the IDs
; below are free for music control:
;
;   Music_Control_ID1     0x4E8  play/stop     byte0: 0x00 stop, N = play song N
;   Music_Control_ID2     0x4E9  master volume byte0: 0..100 (percent, live)
;   Music_Upload_Begin    0x4EC  starts a track upload (see handle-upload-begin)
;   Music_Upload_Chunk    0x4ED  one chunk of a track upload (up to 7 bytes)
;   Music_Upload_End      0x4EE  ends a track upload, length+checksum validated
;
; N on Music_Control_ID1 is checked against resident-song-number (or Axel F's
; reserved number); a command for any other number is ignored and logged.
;
; Play/stop/volume (and the 0x4EF full-status request) echo playback state on
; Music_Status (0x7E6):
;   byte0: play state (0 = stopped, 1 = playing)
;   byte1: current song number (0 if stopped)
;   byte2: master volume, 0..100 %
;
; Upload completion echoes on Music_Upload_Status (0x7E7):
;   byte0: song number, byte1: track slot, byte2: result
;     (0 OK, 1 checksum mismatch, 2 length mismatch, 3 rejected)
;   byte3: reserved
(def music-master-vol 1.0)   ; 0.0-1.0 global multiplier, set live via 0x4E9

; Build and send ECU_status4 (0x7E3) reflecting current KERS state
(defun send-status4 (ebs-en gear-en) {
    (var status-byte 0)
    (setq status-byte (bitwise-or status-byte 0x01))           ; ecu_is_enabled = 1
                                                                 ; ecu_is_disabled = 0 (bit 1)
                                                                 ; boost_mode_enabled = 0 (bit 2)
    (setq status-byte (bitwise-or status-byte 0x08))            ; boost_mode_disabled = 1
    (if (= gear-en 1)
        (setq status-byte (bitwise-or status-byte 0x10))        ; gear_mode_enabled
        (setq status-byte (bitwise-or status-byte 0x20))        ; gear_mode_disabled
    )
    (if (= ebs-en 1)
        (setq status-byte (bitwise-or status-byte 0x40))        ; ebs_enabled
        (setq status-byte (bitwise-or status-byte 0x80))        ; ebs_disabled
    )
    (bufset-u8 dataArray_0x7E3 0 status-byte)
    (can-send-sid 0x7E3 dataArray_0x7E3)
})

; Send ECU_ebs_get (0x7E5) echoing back current KERS parameters
(defun send-ebs-get () {
    (can-send-sid 0x7E5 dataArray_0x7E5)
})

@const-start
; Send Music_Status (0x7E6) echoing back current playback state.
;   byte 0: play state (0 = stopped, 1 = playing)
;   byte 1: current song number (0 if stopped)
;   byte 2: master volume, 0..100 %
(defun send-music-status () {
    (bufset-u8 dataArray_0x7E6 0 (if music-playing 1 0))
    (bufset-u8 dataArray_0x7E6 1 (if music-playing current-song 0))
    (bufset-u8 dataArray_0x7E6 2 (to-i (* music-master-vol 100.0)))
    (can-send-sid 0x7E6 dataArray_0x7E6)
})

; Send Music_Upload_Status (0x7E7) echoing the result of an upload.
;   byte 0: song number, byte 1: track slot, byte 2: result
;     (0 OK, 1 checksum mismatch, 2 length mismatch, 3 rejected)
;   byte 3: reserved
(defun send-upload-status (song-number track-slot result) {
    (bufset-u8 dataArray_0x7E7 0 song-number)
    (bufset-u8 dataArray_0x7E7 1 track-slot)
    (bufset-u8 dataArray_0x7E7 2 result)
    (bufset-u8 dataArray_0x7E7 3 0)
    (can-send-sid 0x7E7 dataArray_0x7E7)
})
@const-end

; Map a VESC fault code onto the 0x7E1 numbering, which is the same number as
; the dashboard LED blink code. Unmapped faults collapse to 16, internal 15V.
(defun map-vesc-fault (v)
    (cond
        ((= v 0) 0)
        ((= v 1) 1)   ; over-voltage
        ((= v 2) 2)   ; under-voltage
        ((= v 3) 6)   ; DRV / gate driver -> MOSFET
        ((= v 4) 4)   ; abs over-current -> motor stalled
        ((= v 5) 11)  ; over-temp FET -> controller over-heat
        ((= v 6) 13)  ; over-temp motor -> motor temperature protection
        (t 16)
    )
)

; Send ECU_status2 (0x7E1): temperature + fault code.
;   byte 0   = ECU/FET temperature (°C, i8)
;   byte 1   = reserved
;   bytes 2-5 = fault code (u32, big-endian)
(defun send-status2 () {
    (bufset-i8 dataArray_0x7E1 0 (to-i (get-temp-fet)))
    (bufset-u8 dataArray_0x7E1 1 0)
    (bufset-u32 dataArray_0x7E1 2 (map-vesc-fault (get-fault)))
    (can-send-sid 0x7E1 dataArray_0x7E1)
})

; Send ECU_status5 (0x7E8): firmware identification block.
;   bytes 0-3 = warranty date, unused
;   bytes 4-7 = see the BCD constants above
(defun send-firmware-id () {
    (bufset-u32 dataArray_0x7E8 0 0)
    (bufset-u8 dataArray_0x7E8 4 motor-power-kw)
    (bufset-u8 dataArray_0x7E8 5 max-speed-kmh)
    (bufset-u8 dataArray_0x7E8 6 sw-base-version)
    (bufset-u8 dataArray_0x7E8 7 sw-app-version)
    (can-send-sid 0x7E8 dataArray_0x7E8)
})

; Send the ECU configuration block (0x7E9-0x7EF) from the live motor config.
; 10mV / 10mA units on the wire. conf-get has to be called from here, not at
; script load: these names resolve fine at runtime, but a top-level read errors.
(defun send-config-block () {
    (bufset-u16 dataArray_0x7E9 0 (to-i (* (conf-get 'l-max-vin) 100)))
    (can-send-sid 0x7E9 dataArray_0x7E9)
    (sleep 0.005)

    (bufset-u16 dataArray_0x7EA 0 (to-i (* (conf-get 'l-min-vin) 100)))
    (can-send-sid 0x7EA dataArray_0x7EA)
    (sleep 0.005)

    ; No VESC equivalent, report 100% so nothing infers a derate.
    (bufset-u8 dataArray_0x7EB 0 100)
    (can-send-sid 0x7EB dataArray_0x7EB)
    (sleep 0.005)

    ; Rolling circumference in cm, with the same 1.03 calibration send_stats
    ; applies to the reported km/h.
    (bufset-u8 dataArray_0x7EC 0
        (to-i (/ (* (conf-get 'si-wheel-diameter) 3.14159 100) speed-cal)))
    (can-send-sid 0x7EC dataArray_0x7EC)
    (sleep 0.005)

    (bufset-u16 dataArray_0x7EE 0 (to-i (* (conf-get 'l-current-max) 100)))
    (can-send-sid 0x7EE dataArray_0x7EE)
    (sleep 0.005)

    ; No separate startup current on VESC, mirror the peak limit.
    (bufset-u16 dataArray_0x7EF 0 (to-i (* (conf-get 'l-current-max) 100)))
    (can-send-sid 0x7EF dataArray_0x7EF)
    (sleep 0.005)
})

; ===== Music: runtime state (RAM) =====
(def num-chan 4)
; current-song is the stop/switch signal AND the number echoed on 0x7E6:
;   0     = stopped  — track threads exit their loops when they see this
;   N>0   = playing song N — each thread loops while (= current-song its-own-id)
(def music-playing false)
(def current-song 0)
(def music-t-start (systime))

; Music functions live in flash (@const-start/@const-end), not the RAM cell
; heap: upstream alone already fills about half the heap, and the music code
; on top of it ran the heap out. Only function definitions go in const
; blocks — anything mutated at runtime (channels, flags, buffers) stays in RAM.
@const-start

; ===== Music: polyphony engine =====

; Scale a track volume by MIDI note velocity. velocity 100 = full volume;
; lower velocities attenuate individual notes.
(defun vscale (vol vel) {
    (var f (/ (to-float vel) 100.0))
    (* vol (if (> f 1.0) 1.0 f))
})

(defun note-on (note vol decay)
    (atomic (loopforeach ch channels {
        (if (= (ix ch 1) 0) {
            (var freq (* 440.0 (pow 2.0 (/ (- note 69.0) 12.0))))
            (var v (* vol music-master-vol))   ; apply live master volume
            (setix ch 2 freq)
            (setix ch 3 v)
            (setix ch 4 decay)
            (setix ch 1 note)
            (foc-play-tone (ix ch 0) freq v)
            (break)
        })
    })))

(defun note-off (note)
    (atomic (loopforeach ch channels {
        (if (= (ix ch 1) note) {
            (setix ch 1 0)
            (foc-play-tone (ix ch 0) 500 0)
            (break)
        })
    })))

; Exponential volume decay thread — sustain envelope for active notes
(defun decay-loop ()
    (loopwhile t {
        (loopforeach ch channels
            (atomic (if (not (= (ix ch 1) 0)) {
                (setix ch 3 (* (ix ch 3) (ix ch 4)))
                (foc-play-tone (ix ch 0) (ix ch 2) (ix ch 3))
            })))
        (sleep 0.02)
    }))

; ===== Music: playback =====

; song-id is baked in at spawn time so the thread knows when to exit
(defun play-midi-track (parser-idx volts decay song-id) {
    (var t-delta 0.0)
    (var midi-tdiv 100)
    (var midi-tempo 400000)
    (var t-offset -1.0)  ; set on first note to skip any silent intro
    (loopwhile (= current-song song-id)
        (match (ext-midi-parse parser-idx)
            ((midi-header (? size) (? format) (? tracks) (? time-div)) {
                (setq midi-tdiv time-div)
            })
            ((midi-track-midi (? time) (? status) (? chan) (? p1) (? p2)) {
                (var secs (/ (* midi-tempo (to-float time)) midi-tdiv 1000000.0))
                (setq t-delta (+ t-delta secs))
                ; On first event, anchor t-offset so the track starts immediately
                (if (= t-offset -1.0) (setq t-offset t-delta))
                (var to-sleep (- (- t-delta t-offset) (secs-since music-t-start)))
                ; Chunked sleep: wake every 50ms to check if song was stopped/changed.
                ; Prevents a long inter-note gap from delaying the stop response.
                (loopwhile (and (> to-sleep 0.05) (= current-song song-id)) {
                    (sleep 0.05)
                    (setq to-sleep (- to-sleep 0.05))
                })
                (if (and (> to-sleep 0.001) (= current-song song-id)) (sleep to-sleep))
                ; Check again before firing the note — avoids the stray note after stop
                (if (!= current-song song-id) (break))
                (if (= status 9) (note-on p1 (vscale volts p2) decay))
                (if (= status 8) (note-off p1))
            })
            ((midi-track-meta (? time) (? type) (? len) (? data)) {
                (if (= type 81) (setq midi-tempo data))
            })
            (midi-error { (print "MIDI error") (break) })
            (midi-eob (break))
            (_ nil)   ; other events (track headers etc.) need no handling
        )
    )
})

(defun stop-music () {
    (def current-song 0)        ; all track threads exit at next loop check
    (sleep 0.05)                ; give them time to finish current sleep/parse
    (foc-play-stop)
    (loopforeach ch channels { (setix ch 1 0) })
    (def music-playing false)
})

; Plays whatever's currently loaded in song-buf-0/1, according to which track
; slots handle-upload-end has marked ready.
(defun play-current-song () {
    (def current-song 0)        ; stop any running tracks
    (sleep 0.05)                ; wait before re-opening parsers
    (if (or song-buf-0-ready song-buf-1-ready) {
        (if song-buf-0-ready (ext-midi-open 0 song-buf-0))
        (if song-buf-1-ready (ext-midi-open 1 song-buf-1))
        ; Set timing reference after parsers are loaded — avoids startup delay
        (def music-t-start (systime))
        (def current-song resident-song-number)   ; set before spawning so threads see it immediately
        (if song-buf-0-ready (spawn 200 play-midi-track 0 song-buf-0-vol song-buf-0-decay resident-song-number))
        (if song-buf-1-ready (spawn 200 play-midi-track 1 song-buf-1-vol song-buf-1-decay resident-song-number))
        (def music-playing true)
    } {
        (print "No song buffer ready to play")
    })
})

; Axel F bypasses the streamed-buffer model — see the import comment at the
; top. Same shape as play-current-song, hardcoded to the package buffers.
(defun play-axelf () {
    (def current-song 0)
    (sleep 0.05)
    (ext-midi-open 0 song1-track0)
    (ext-midi-open 1 song1-track1)
    (def music-t-start (systime))
    (def current-song axelf-song-number)
    (spawn 200 play-midi-track 0 2 0.92 axelf-song-number)
    (spawn 200 play-midi-track 1 1.0 0.92 axelf-song-number)
    (def music-playing true)
})

; ===== Music: song upload (streaming) =====
; Music_Upload_Begin (0x4EC) — starts a new track upload.
;   byte0 = song number, byte1 = track slot (0 melody/1 bass),
;   byte2-3 = length LE u16, byte4 = volume x10, byte5 = decay x100
(defun handle-upload-begin (data) {
    (var song-number (bufget-u8 data 0))
    (var track-slot  (bufget-u8 data 1))
    (var track-len (+ (bufget-u8 data 2) (* (bufget-u8 data 3) 256)))
    (var vol-x10 (bufget-u8 data 4))
    (var decay-x100 (bufget-u8 data 5))
    (if (or (> track-slot 1) (> track-len song-buf-max)) {
        (print (list "upload begin rejected: slot" track-slot "len" track-len))
        (send-upload-status song-number track-slot 3)
    } {
        ; The buffers about to be refilled may be mid-playback — stop first,
        ; or a play-midi-track thread would read them while they're rewritten.
        (if music-playing (stop-music))
        ; A new song number clears BOTH track-ready flags, so a mono song
        ; doesn't inherit a stale second track from a previous song.
        (if (!= song-number resident-song-number) {
            (def song-buf-0-ready false)
            (def song-buf-1-ready false)
        })
        (def upload-active-slot track-slot)
        (def upload-checksum 0)
        (def upload-received 0)
        (if (= track-slot 0) {
            (def song-buf-0-len track-len)
            (def song-buf-0-vol (/ (to-float vol-x10) 10.0))
            (def song-buf-0-decay (/ (to-float decay-x100) 100.0))
            (def song-buf-0-ready false)
        } {
            (def song-buf-1-len track-len)
            (def song-buf-1-vol (/ (to-float vol-x10) 10.0))
            (def song-buf-1-decay (/ (to-float decay-x100) 100.0))
            (def song-buf-1-ready false)
        })
        (def resident-song-number song-number)
    })
})

; Music_Upload_Chunk (0x4ED) — byte0 = sequence (informational only; chunks
; are appended in arrival order), byte1-7 = up to 7 bytes.
(defun handle-upload-chunk (data) {
    (if (and (>= upload-active-slot 0) (> (buflen data) 1)) {
        (var n (- (buflen data) 1))
        (var dst (if (= upload-active-slot 0) song-buf-0 song-buf-1))
        (looprange i 0 n {
            (var b (bufget-u8 data (+ i 1)))
            (if (< (+ upload-received i) song-buf-max) {
                (bufset-u8 dst (+ upload-received i) b)
                (def upload-checksum (mod (+ upload-checksum b) 256))
            })
        })
        (def upload-received (+ upload-received n))
    })
})

; Music_Upload_End (0x4EE) — byte0 = song number, byte1 = track slot,
; byte2 = checksum, byte3 = reserved. Validates length+checksum and, on
; success, marks that track slot ready to play. On failure music-service
; retries the whole track from Begin.
(defun handle-upload-end (data) {
    (var song-number (bufget-u8 data 0))
    (var track-slot  (bufget-u8 data 1))
    (var checksum    (bufget-u8 data 2))
    (var expected-len (if (= track-slot 0) song-buf-0-len song-buf-1-len))
    (cond
        ((< upload-active-slot 0) (send-upload-status song-number track-slot 3))
        ; Must match the slot Begin opened, or a stale/reordered End could
        ; mark the wrong slot ready using leftover length/checksum.
        ((!= track-slot upload-active-slot) {
            (print (list "upload end: slot mismatch, expected" upload-active-slot "got" track-slot))
            (send-upload-status song-number track-slot 3)
        })
        ((!= upload-received expected-len) {
            (print (list "upload end: length mismatch" upload-received "!=" expected-len))
            (send-upload-status song-number track-slot 2)
        })
        ((!= upload-checksum checksum) {
            (print (list "upload end: checksum mismatch" upload-checksum "!=" checksum))
            (send-upload-status song-number track-slot 1)
        })
        (t {
            (if (= track-slot 0) (def song-buf-0-ready true) (def song-buf-1-ready true))
            (def upload-active-slot -1)
            (send-upload-status song-number track-slot 0)
        })
    )
})

; Music CAN frames, called from proc-sid. data is freed by proc-sid.
(defun proc-music (id data) {
    ; Music_Control_ID1 (0x4E8) - play/stop
    ; 4E8#00 = stop, 4E8#<N> = play song N (N = resident-song-number or
    ; axelf-song-number; anything else is ignored and logged)
    (if (= id 0x4E8u32) {
        (var cmd (bufget-u8 data 0))
        (cond
            ((= cmd 0x00) (stop-music))
            ((= cmd axelf-song-number) (play-axelf))
            ((= cmd resident-song-number) (play-current-song))
            (t (print (list "Music cmd" cmd "ignored: no matching resident song"))))
        (send-music-status)
    })

    ; Music_Control_ID2 (0x4E9) - master volume
    ; 4E9#<0..100 in hex>  (e.g. 4E9#50 = 80%)
    (if (= id 0x4E9u32) {
        (var vol-pct (bufget-u8 data 0))
        (if (> vol-pct 100) (setq vol-pct 100))   ; clamp
        (def music-master-vol (/ (to-float vol-pct) 100.0))
        (send-music-status)
    })

    ; Music_Upload_Begin (0x4EC) / Chunk (0x4ED) / End (0x4EE)
    (if (= id 0x4ECu32) (handle-upload-begin data))
    (if (= id 0x4EDu32) (handle-upload-chunk data))
    (if (= id 0x4EEu32) (handle-upload-end data))
})
@const-end

; Handler for SID CAN-frames
(defun proc-sid (id data) {

    ; Control_Message_ID4 (0x4E0) - KERS/boost/gear enable
    ; Byte 0: bit0=gear_mode_enable, bit1=boost_mode_enable, bit2=ebs_enable
    (if (= id 0x4E0u32) {
        (var ctrl-byte (bufget-u8 data 0))
        (var gear-en  (bitwise-and ctrl-byte 0x01))
        (var boost-en (shr (bitwise-and ctrl-byte 0x02) 1))
        (var ebs-en   (shr (bitwise-and ctrl-byte 0x04) 2))

        (def kers-enabled (= ebs-en 1))
        (print (list "KERS:" kers-enabled "gear:" gear-en "boost:" boost-en))

        ; The battery-side regen limit (l-in-current-min, negative = regen
        ; direction, 0.0 = none allowed) is applied by the main loop so the
        ; voltage taper tracks the live pack voltage; only the state changes
        ; here.
        (print (list "Regen target:" (if kers-enabled "on" "off")))

        ; Report status4 back so the service sees the correct state
        (send-status4 ebs-en gear-en)
    })

    ; ECU_ebs_set (0x4E2) - KERS voltage/current parameters
    ; ecu-service sends values in 10mV/10mA units, multiply by 10 to get mV/mA
    (if (= id 0x4E2u32) {
        (var raw-voltage (bufget-u16 data 0))
        (var raw-current (bufget-u16 data 2))
        (def kers-voltage (* raw-voltage 10))
        (def kers-current (* raw-current 10))
        (print (list "KERS params V:" kers-voltage "mV, I:" kers-current "mA"))

        ; Echo the incoming values straight back on 0x7E5. Both the request
        ; (0x4E2) and the report (0x7E5) use 10mV / 10mA units on the wire,
        ; so the raw u16 from data is already in the right scale; applying
        ; a second x10 here would overflow the u16 and produce nonsense at
        ; the receiver.
        (bufset-u16 dataArray_0x7E5 0 raw-voltage)
        (bufset-u16 dataArray_0x7E5 2 raw-current)

        ; Immediately echo back ebs_get
        (send-ebs-get)
    })

    ; Control_Message_ID2 (0x4EF) - Report all ECU settings
    (if (= id 0x4EFu32) {
        (print "Full status request (0x4EF)")
        (send_stats)
        (sleep 0.01)
        (send-status4
            (if kers-enabled 1 0)
            1  ; gear_mode_enable is always true per KERS_GEAR_MODE_ENABLE
        )
        (sleep 0.01)
        (send-ebs-get)
        (sleep 0.01)
        (send-firmware-id)
        (sleep 0.01)
        (send-config-block)
        (sleep 0.01)
        (send-music-status)
    })

    ; Control_Message_ID1 (0x4EB) - Speed limit ratio
    (if (= id 0x4EBu32) {
        (var limit-pct (bufget-u8 data 0))
        (print (list "Speed limit:" limit-pct "%"))
        ; TODO: apply speed limit if needed
    })

    ; Control_Message_ID3 (0x4E1) - Gear set
    (if (= id 0x4E1u32) {
        (var gear (bufget-u8 data 0))
        (print (list "Gear set:" gear))
        ; TODO: apply gear mode if needed
    })

    ; Music control and song upload frames (0x4E8/0x4E9/0x4EC-0x4EE)
    (proc-music id data)

    (free data)
})

; Event handler thread
(defun event-handler ()
    (loopwhile t
        (recv
            ((event-can-sid (? id) . (? data)) (proc-sid id data))
            (_ nil)
        )
    )
)

(defun send_stats () {

    (bits-enc-int statusByte_1 0 1 2)
    (bits-enc-int statusByte_1 1 0 2)
    (bits-enc-int statusByte_1 2 0 2)
    (bits-enc-int statusByte_1 3 1 1)
    (bits-enc-int statusByte_1 4 0 1)
    (bits-enc-int statusByte_1 5 1 1)
    (bits-enc-int statusByte_1 6 1 1)
    (bits-enc-int statusByte_1 7 0 1)

    ; Voltage: (get-vin) returns V, ecu-service expects big-endian u16 in 10mV units
    (bufset-u16 dataArray_0x7E0 0 (to-i (* (get-vin) 100.0)))

    ; Current: (get-current-in) returns A, ecu-service expects big-endian i16 in 10mA units
    ; Negative values = regen (charging battery)
    (bufset-i16 dataArray_0x7E0 2 (to-i (* (get-current-in) 100.0)))

    (var speedhere (/ (* (get-speed) 3.6) speed-cal))

    (bufset-u16 dataArray_0x7E0 4 (to-i (/ (to-float (abs (get-rpm))) pole-pairs)))
    (bufset-u8 dataArray_0x7E0 6 speedhere)

    ; Flags: bit0 = throttle, bit1 = brake (regen active)
    (var flags 0)
    (if (> (get-current-in) 5.0)
        (setq flags (bitwise-or flags 0x01))
    )
    (if (< (get-current-in) -0.5)
        (setq flags (bitwise-or flags 0x02))
    )
    (bufset-u8 dataArray_0x7E0 7 flags)

    (bufset-u32 dataArray_0x7E2 0 (to-i (/ (to-float (sysinfo 'odometer)) 107.0)))

    (can-send-sid 0x7E0 dataArray_0x7E0)
    (sleep 0.01)
    (can-send-sid 0x7E2 dataArray_0x7E2)
    (sleep 0.01)
})

; Scale motor brake current with speed so KERS eases out at very low speed
; instead of riding the sensor handover. l-in-current-min alone does not do
; this: at low ERPM hardly any current makes it back to the battery, so the
; input limit never bites and the motor keeps getting full brake current right
; through the handover and down to standstill.
; Forward drive keeps the full configured motor-current envelope at any speed.
; Without drive, apply the standstill cutoff and KERS speed fade.
(defun kers-fade-target (erpm)
    (if (drive-requested)
        1.0
        (if (< erpm kers-hold-cutoff-erpm)
            0.0
            (if (not kers-enabled)
                1.0
                (if (< erpm kers-fade-end-erpm)
                    kers-floor
                    (if (> erpm kers-fade-start-erpm)
                        1.0
                        (+ kers-floor
                           (* (- 1.0 kers-floor)
                              (/ (- erpm kers-fade-end-erpm)
                                 (- kers-fade-start-erpm kers-fade-end-erpm))))
                    )
                )
            )
        )
    )
)

; Slew-limit toward the target so noisy ERPM cannot chatter the limit.
(defun update-kers-fade () {
    (var target (kers-fade-target (abs (to-float (get-rpm)))))
    (var d (- target kers-scale))
    (var new (if (drive-requested)
        target
        (if (> (abs d) kers-scale-slew)
            (+ kers-scale (if (> d 0.0) kers-scale-slew (- 0.0 kers-scale-slew)))
            target)))
    (if (> (abs (- new kers-scale)) 0.005) {
        (def kers-scale new)
        (conf-set 'l-current-min-scale new)
    })
})

; Battery-side regen voltage taper: full authority at or below the 0x4E2
; target, linear to zero across the taper band above it.
(defun kers-voltage-scale (vin-mv)
    (if (< vin-mv kers-voltage)
        1.0
        (if (> vin-mv (+ kers-voltage kers-voltage-taper))
            0.0
            (/ (- (+ kers-voltage kers-voltage-taper) vin-mv)
               kers-voltage-taper)
        )
    )
)

; Drive commands permit bounded regen regardless of speed or KERS enable.
; Brake commands use the 0x4E2 current while KERS is on, zero otherwise.
; Both envelopes respect the live pack-voltage taper.
(defun update-kers-current () {
    (var target (if kers-enabled
        (- 0.0 (/ (to-float kers-current) 1000.0))
        0.0))
    (if (and (drive-requested) (> target kers-drive-regen-floor))
        (setq target kers-drive-regen-floor))
    (setq target (* target (kers-voltage-scale (* (get-vin) 1000.0))))
    (if (> (abs (- target kers-amps-now)) 0.05) {
        (def kers-amps-now target)
        (conf-set 'l-in-current-min target)
    })
})

; Save the odometer once when the shutdown signal drops (power loss)
(defun check-shutdown () {
    (var v (get-adc 2))
    (if (and (< v shutdown-threshold) (not shutdown-triggered)) {
        (def shutdown-triggered true)
        (eeprom-store-f 0 (to-float (sysinfo 'odometer)))
    })
})

; ===== Boot =====
; image-save stores everything defined above in flash; from the next boot on the
; firmware re-creates that environment and calls main instead of parsing this
; file again. That is what lets the @const blocks above survive a power-on
; boot (re-parsing rewrote them and failed). Everything that touches hardware or
; must be fresh each boot (CAN buffers, config, native libs, threads) lives in
; main, since none of that is restored from the image.
(defun main () {
    (def dataArray_0x7E0 (array-create 8))
    (def dataArray_0x7E1 (array-create 6))
    (def dataArray_0x7E2 (array-create 8))
    (def dataArray_0x7E3 (array-create 8))
    (def dataArray_0x7E4 (array-create 8))
    (def dataArray_0x7E5 (array-create 4))
    (def dataArray_0x7E6 (array-create 3))   ; music: Music_Status
    (def dataArray_0x7E7 (array-create 4))   ; music: Music_Upload_Status
    (def dataArray_0x7E8 (array-create 8))
    (def dataArray_0x7E9 (array-create 2))
    (def dataArray_0x7EA (array-create 2))
    (def dataArray_0x7EB (array-create 1))
    (def dataArray_0x7EC (array-create 1))
    (def dataArray_0x7EE (array-create 2))
    (def dataArray_0x7EF (array-create 2))

    (def song-buf-0 (array-create song-buf-max))  ; track slot 0: melody
    (def song-buf-1 (array-create song-buf-max))  ; track slot 1: bass
    (def channels (map (fn (x) (list x 0 0 0 0)) (range num-chan)))

    ; The VESC keeps its config while powered, so after a lisp restart the limits
    ; would hold stale values (e.g. a regen limit armed by a handshake that the
    ; fresh script never saw). Force the safe boot state: no battery regen and no
    ; brake current until the loops take ownership and the vehicle asks for it.
    (conf-set 'l-in-current-min 0.0)
    (conf-set 'l-current-min-scale 0.0)

    ; Einmalig nach dem Einschalten Betriebsbereitschaft bestätigen
    (bufset-u8 dataArray_0x7E4 0 1)
    (bufset-u8 dataArray_0x7E4 1 100)
    (bufset-u8 dataArray_0x7E4 2 100)
    (bufset-u8 dataArray_0x7E4 3 100)
    (bufset-u8 dataArray_0x7E4 4 100)
    (bufset-u8 dataArray_0x7E4 5 100)
    (bufset-u8 dataArray_0x7E4 6 100)
    (bufset-u8 dataArray_0x7E4 7 100)
    (sleep 0.005)
    (can-send-sid 0x7E4 dataArray_0x7E4)

    (load-native-lib midi)
    (ext-midi-init 2) ; 2 simultaneous MIDI parsers = max 2 tracks per song

    ; Sustain envelope for the music engine
    (spawn 200 decay-loop)

    ; Spawn the event handler thread and pass the ID it returns to C
    (event-register-handler (spawn 150 event-handler))

    ; Enable the CAN event for SID frames
    (event-enable 'event-can-sid)

    ; Restore odometer from EEPROM
    (def saved-odo (eeprom-read-f 0))
    (if saved-odo
        (set-odometer (to-i saved-odo))
    )

    ; Send initial status4 as not-kers (ebs_disabled)
    (send-status4 0 1)

    ; Broadcast identity and config at boot. Nothing requests 0x4EF on its own
    ; unless a fault was reported first.
    (sleep 0.01)
    (send-firmware-id)
    (sleep 0.01)
    (send-config-block)

    ; Song list lives in music-service (`music songs`), not here.
    (print "app.lisp loaded")

    ; Main loop: 5ms tick, shutdown check and KERS fade every tick, CAN stats every
    ; 40th tick (200ms), status2 every 200th (1s). A fault repeated faster than 1Hz
    ; keeps resetting the
    ; consumer's 0.5s request timer, so it never asks for the state that would
    ; clear the fault.
    (loopwhile t {
        (check-shutdown)
        (update-kers-fade)
        (update-kers-current)
        (if (= (mod tick 40) 0) {
            (send_stats)
        })
        (if (= (mod tick 200) 0) {
            (send-status2)
        })
        (def tick (+ tick 1))
        (sleep 0.005)
    })
})

; LispBM loads some library helpers from source on first use. Done after an
; image boot, that load writes into the image's flash area at whatever spot is
; next, which a previous boot may already have filled differently ("already
; occupied") and kills the calling thread. Touch every such helper the code
; uses at runtime now, so they are baked into the image instead.
(list abs loopforeach looprange loopwhile)

(image-save)

; Start now as well; on later boots the firmware calls main itself.
(main)
