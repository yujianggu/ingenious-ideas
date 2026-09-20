import pytest
from fastapi import HTTPException
from app.media import validate
from media_fixture import generate


@pytest.mark.parametrize('seconds,approved',[(29,30),(91,90)])
def test_actual_duration_outside_product_bounds_rejected(tmp_path,seconds,approved):
    path=tmp_path/'video.mp4'; generate(path,seconds=seconds)
    with pytest.raises(HTTPException) as error:
        validate(path,'video','video.mp4',{'start':0,'end':approved})
    assert error.value.status_code==422
    assert '30–90' in error.value.detail


def test_actual_30_seconds_valid(tmp_path):
    path=tmp_path/'video.mp4'; generate(path,seconds=30)
    validate(path,'video','video.mp4',{'start':0,'end':30})
