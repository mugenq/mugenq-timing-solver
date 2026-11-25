"""v0.5.2: decimal transport, independent energy layers and spectral detail."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pytest

from speed_controls import (clamp_speed_percent, speed_to_slider, slider_to_speed,
                            MAX_SPEED_PERCENT, SLIDER_UNITS_PER_PERCENT)
from diagnostic_curves import (diagnostic_energy_curves, DIAGNOSTIC_CHANGE_ID,
                               DIAGNOSTIC_WAVE_ID)
from node_engine import AudioData, default_spectral_graph, default_registry, Graph
from spectral_analysis import compute_spectral


def test_speed_decimal_roundtrip_and_caps():
    assert MAX_SPEED_PERCENT==135.0
    assert SLIDER_UNITS_PER_PERCENT==10
    for value in (0.,.1,25.5,55.2,99.9,100.,100.1,134.9,135.):
        assert slider_to_speed(speed_to_slider(value))==value
    assert speed_to_slider(135.0)==1350
    assert speed_to_slider(500.)==1350
    assert clamp_speed_percent(-10.)==0.
    assert clamp_speed_percent(120.06)==120.1
    assert clamp_speed_percent(120.04)==120.0
    assert slider_to_speed(1001)==100.1
    with pytest.raises(ValueError,match='finite'):
        clamp_speed_percent(float('nan'))


def test_diagnostic_energy_layers_show_both_without_modifying_audio():
    # Three attacks, separated by quiet regions, in the same 2-ms bins as UI.
    x=np.zeros(5000,np.float32)
    for index, amplitude in ((250,1.),(1250,.4),(3000,.1)):
        x[index:index+5]=amplitude
    before=x.copy()
    audio=AudioData('reference.wav',10.,-x,x,bin_seconds=.002)
    outputs,labels=diagnostic_energy_curves(audio)
    assert set(outputs)=={DIAGNOSTIC_WAVE_ID, DIAGNOSTIC_CHANGE_ID}
    assert 'Energy Changes' in labels[f'{DIAGNOSTIC_CHANGE_ID}:curve']
    assert 'Wave Energy' in labels[f'{DIAGNOSTIC_WAVE_ID}:curve']
    energy=outputs[DIAGNOSTIC_WAVE_ID]['curve']
    changes=outputs[DIAGNOSTIC_CHANGE_ID]['curve']
    assert len(energy.values)==len(changes.values)==5000
    assert energy.sample_step_s==changes.sample_step_s==.002
    assert np.max(changes.values)>0
    assert np.count_nonzero(changes.values)>0
    assert np.array_equal(x,before)  # non-destructive


def test_spectrogram_detail_limits_and_graph_roundtrip():
    reg=default_registry()
    spec=reg.defs['signal.spectrogram']
    param=next(p for p in spec.parameters if p.name=='hop_ms')
    assert param.minimum<=1.5
    graph=default_spectral_graph(reg)
    source=next(n for n in graph.nodes.values() if n.type_id=='signal.spectrogram')
    assert source.params['hop_ms']==6.0
    assert any(n.type_id=='candidates.burst_cleanup' for n in graph.nodes.values())
    for detail,hop in ((1,6.0),(2,3.0),(4,1.5)):
        source.params['hop_ms']=hop
        restored=Graph.from_dict(reg,graph.to_dict())
        assert restored.nodes[source.id].params['hop_ms']==hop


def test_real_spectral_hop_density_and_window_unchanged():
    rate=48000
    t=np.arange(rate,dtype=np.float32)/rate
    pcm=(np.sin(2*np.pi*330*t)*.1).astype(np.float32)
    results={}
    for hop in (6.,3.,1.5):
        result=compute_spectral(pcm,rate,hop_ms=hop,window_ms=46.,
                                window_mode='legacy_fft')
        results[hop]=result
        assert result.window_samples==4096
        assert result.fft_samples==4096
        assert result.hop_samples==round(rate*hop/1000)
        assert result.harmonic.shape[1]==len(result.center_samples)
    assert results[3.].harmonic.shape[1]==2*results[6.].harmonic.shape[1]
    # Endpoint inclusion makes the 4× count 667 rather than 668 for 1 s.
    assert abs(results[1.5].harmonic.shape[1]-4*results[6.].harmonic.shape[1]) <= 1
