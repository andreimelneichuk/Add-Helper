import pytest
from evals.images import image_price_micro


def test_image_price_counts_megapixels_and_flat_prices_up() -> None:
    flux = {"pricing": [{"billable": "output_image", "unit": "megapixel", "cost_usd": 0.03}]}
    recraft = {"pricing": [{"billable": "output_image", "unit": "image", "cost_usd": 0.035}]}
    assert image_price_micro(flux) == 33_000
    assert image_price_micro(recraft) == 35_000


def test_image_price_ignores_input_images_and_refuses_unknown() -> None:
    flex = {
        "pricing": [
            {"billable": "input_image", "unit": "megapixel", "cost_usd": 0.06},
            {"billable": "output_image", "unit": "megapixel", "cost_usd": 0.06},
        ]
    }
    assert image_price_micro(flex) == 66_000
    with pytest.raises(SystemExit):
        image_price_micro({"pricing": []})
