from app import create_app


def test_index_shows_page():
    response = create_app().test_client().get("/")
    assert response.status_code == 200
    assert "SATurn" in response.get_data(as_text=True)
