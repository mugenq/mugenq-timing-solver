"""Regression: playhead must use output frames at the speed they were rendered."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from played_frame_clock import PlayedFrameClock
from playback_clock import PlaybackClock


def test_old_speed_buffer_remains_old_speed_after_user_selects_half_speed():
    c=PlayedFrameClock(48000)
    c.append(4800,0,4800)     # 100ms queued at 1x
    c.append(4800,4800,7200)  # 100ms queued at 0.5x
    assert c.source_at_processed_us(50000)==pytest.approx(2400)
    assert c.source_at_processed_us(100000)==pytest.approx(4800)
    assert c.source_at_processed_us(150000)==pytest.approx(6000)
    assert c.source_at_processed_us(200000)==pytest.approx(7200)
    assert c.speed_regions==2


def test_half_speed_from_start_and_seek_reset():
    c=PlayedFrameClock(44100,44100*32)
    c.append(4410,44100*32,44100*32+2205)
    assert c.source_at_processed_us(0)==pytest.approx(1411200)
    assert c.source_at_processed_us(100000)==pytest.approx(1413405)
    after_seek=PlayedFrameClock(44100,44100*60)
    after_seek.append(4410,44100*60,44100*60+2205)
    assert after_seek.source_at_processed_us(50000)==pytest.approx(44100*60+1102.5)


def test_consecutive_buffers_same_rate_coalesce_and_output_clock_clamps():
    c=PlayedFrameClock(48000)
    for i in range(1000):
        c.append(96,i*48,(i+1)*48)
    assert c.speed_regions==1
    assert c.queued_output_frames==96000
    assert c.source_at_processed_us(2000000)==pytest.approx(48000)
    assert c.source_at_processed_us(20000000)==pytest.approx(48000)


def test_frame_mapping_supports_non_integer_source_positions():
    c=PlayedFrameClock(48000,100.25)
    c.append(4,100.25,101.25)
    assert c.source_at_frames(2)==pytest.approx(100.75)


def test_media_clock_snaps_to_slow_audio_backend_instead_of_compounding_lag():
    now=[0.0]
    c=PlaybackClock(now=lambda:now[0]);c.reset(5.0,duration=20)
    c.set_rate(.25);c.set_playing(True)
    for i in range(1,31):
        now[0]=i*.05
        real=5+now[0]*.25
        c.sync(real)
        assert abs(c.displayed()-real)<.002


def test_paused_and_seeked_clock_remains_stable():
    now=[0.0]
    c=PlaybackClock(now=lambda:now[0]);c.reset(10,duration=20)
    c.set_playing(True);c.set_rate(.5);now[0]+=.1
    c.set_playing(False);p=c.displayed()
    now[0]+=10;assert c.displayed()==pytest.approx(p)
    c.reset(2);assert c.displayed()==2


def test_multiple_playback_speeds_are_mapped_from_actual_queued_buffers():
    c=PlayedFrameClock(48000)
    c.append(4800,0,4800)        # first 100ms at 1x
    c.append(4800,4800,6000)     # next 100ms at 0.25x
    c.append(4800,6000,10800)    # next 100ms at 1x
    assert c.source_at_processed_us(100000)==pytest.approx(4800)
    assert c.source_at_processed_us(200000)==pytest.approx(6000)
    assert c.source_at_processed_us(250000)==pytest.approx(8400)
