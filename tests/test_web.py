from fastapi.testclient import TestClient

from music_video_grabber.main import app


def test_dashboard_renders() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Music Video Grabber" in response.text
