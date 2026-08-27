from defossil.__main__ import main


class TestEntryPoint:
    def test_main_is_callable(self) -> None:
        assert callable(main)
