"""v0.4.2 audio buffer, timing instrumentation, and no-temp-file regressions."""
from pathlib import Path
import sys
import wave
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from streaming_pcm import PCMBufferMixer,decode_stereo
from timing_diagnostics import TimingMonitor,confidence_histogram


def pcm_int16(buf):
    return np.frombuffer(buf,dtype='<i2').reshape(-1,2)


def test_stream_pcm_stereo_preserves_audio_samples_at_1x():
    source=np.array([[.1,-.2],[.4,-.5],[.2,-.1],[0.,0.]],dtype=np.float32)
    mixer=PCMBufferMixer(source,48000,[],1.,0.)
    out=pcm_int16(mixer.render(3))
    assert out.shape==(3,2)
    np.testing.assert_allclose(out,source[:3]*32767,atol=1.3)
    assert mixer.seconds==pytest.approx(3/48000)
    assert len(mixer.render(9))==4
    assert mixer.render(1)==b''


def test_stream_click_is_sample_aligned_across_small_buffers():
    rate=48000
    source=np.zeros((128,2),dtype=np.float32)
    mixer=PCMBufferMixer(source,rate,[(29/rate,1800.,1.)],0.,1.)
    a=pcm_int16(mixer.render(32))
    b=pcm_int16(mixer.render(32))
    c=pcm_int16(mixer.render(64))
    arr=np.concatenate((a,b,c))
    assert not arr[:29].any()
    assert np.max(abs(arr[30:32]))>0
    assert np.max(abs(arr[32:45]))>0
    assert np.max(abs(arr[65:100]))>0


def test_stream_edit_changes_future_buffer_without_redecode_or_file(tmp_path):
    audio=np.zeros((1000,2),dtype=np.float32)
    mixer=PCMBufferMixer(audio,48000,[],0.,.8)
    mixer.render(100)
    mixer.set_events([(200/48000,1800.,1.)])
    a=pcm_int16(mixer.render(200))
    assert np.max(abs(a[:100]))==0
    assert np.max(abs(a[101:]))>0
    mixer.set_events([])
    assert not pcm_int16(mixer.render(120)).any()
    assert not list(tmp_path.iterdir())  # mixer is an in-memory audio path


def test_stream_seek_rate_and_volume():
    arr=np.arange(0,100,dtype=np.float32)/200
    audio=np.column_stack((arr,-arr)).astype(np.float32)
    mix=PCMBufferMixer(audio,48000,[],1.,0.)
    mix.set_speed(2.)
    out=pcm_int16(mix.render(4))
    np.testing.assert_allclose(out[:,0],arr[[0,2,4,6]]*32767,atol=1.1)
    mix.seek(2/48000)
    mix.set_speed(.5)
    out=pcm_int16(mix.render(4))
    np.testing.assert_allclose(out[:,0],np.interp([2,2.5,3,3.5],np.arange(100),arr)*32767,atol=1.1)
    mix.set_gains(0.,0.)
    assert not pcm_int16(mix.render(4)).any()
    mix.set_speed(0.)
    assert mix.render(32)==b''
    with pytest.raises(ValueError):mix.set_speed(100)
    with pytest.raises(ValueError):mix.set_gains(-1,0)


def test_dense_hitsound_events_can_overlap():
    arr=np.zeros((100,2),dtype=np.float32)
    mix=PCMBufferMixer(arr,8000,[(10/8000,1000,1.),(11/8000,2000,1.)],0.,1.)
    out=pcm_int16(mix.render(100))
    assert not out[:10].any()
    assert abs(out[12,0])>0


def test_decode_stereo_budget_cancellation_and_no_disk(monkeypatch,tmp_path):
    import types
    class FakeFormat:FLOAT32=1
    monkeypatch.setitem(sys.modules,'miniaudio',types.SimpleNamespace(
        SampleFormat=FakeFormat,
        get_file_info=lambda _:types.SimpleNamespace(sample_rate=48000),
        stream_file=lambda *_args,**_kw:iter([np.zeros(800,dtype=np.float32)])))
    rate,samples=decode_stereo(tmp_path/'nonexistent.fake')
    assert rate==48000 and samples.shape==(400,2)
    with pytest.raises(MemoryError):decode_stereo('placeholder',max_bytes=16)
    with pytest.raises(InterruptedError):decode_stereo('placeholder',cancelled=lambda:True)
    assert not list(tmp_path.iterdir())


def test_timing_monitor_reports_actual_callbacks_not_screen_hz():
    clock=[0.]
    meter=TimingMonitor(now=lambda:clock[0])
    for i in range(101):
        clock[0]=i*.01
        meter.record_paint()
        if i%2==0:meter.record_timer()
        meter.record_backend(.5+.005*i,.5+.005*i+.004)
    snap=meter.snapshot()
    assert snap.paint_hz==pytest.approx(100.)
    assert snap.timer_hz==pytest.approx(50.)
    assert snap.paint_p95_ms==pytest.approx(10.)
    assert snap.clock_disagreement_p95_ms==pytest.approx(4.)
    assert snap.backend_samples==101


def test_confidence_histogram_has_no_probability_claim():
    bins=confidence_histogram([0,0.25,.5,.75,1,np.nan],20)
    assert bins.sum()==5 and bins[0]>=1 and bins[-1]>=1
    assert not confidence_histogram([],20).any()
    with pytest.raises(ValueError):confidence_histogram([.2],0)
