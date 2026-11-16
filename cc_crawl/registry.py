"""Spider 注册表：发现并按名字管理一个项目里的所有爬虫。"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
from pathlib import Path
from typing import Iterable, Iterator

from .exceptions import SpiderNotFound
from .spider import Spider


class SpiderRegistry:
    """爬虫名 → Spider 类的注册表。

    适合一个项目里有几十上百个爬虫的场景：启动时扫描目录/包，任务到来时按名字取类。
    """

    def __init__(self, spiders: Iterable[type[Spider]] = ()) -> None:
        self._spiders: dict[str, type[Spider]] = {}
        for spider_cls in spiders:
            self.register(spider_cls)

    def register(self, spider_cls: type[Spider]) -> None:
        if not (isinstance(spider_cls, type) and issubclass(spider_cls, Spider)):
            raise TypeError(f"{spider_cls!r} 不是 Spider 的子类")
        if spider_cls is Spider:
            raise ValueError("不能注册 Spider 基类本身")
        name = getattr(spider_cls, "name", "")
        if not name:
            raise ValueError(f"{spider_cls.__name__} 没有定义 name")
        if name in self._spiders:
            existing = self._spiders[name]
            raise ValueError(
                f"爬虫名重复：{name!r}（{existing.__module__}.{existing.__name__} 与 "
                f"{spider_cls.__module__}.{spider_cls.__name__}）"
            )
        self._spiders[name] = spider_cls

    def get(self, name: str) -> type[Spider]:
        try:
            return self._spiders[name]
        except KeyError as exc:
            raise SpiderNotFound(f"没有找到爬虫 {name!r}，可用：{', '.join(self.names)}") from exc

    @property
    def names(self) -> list[str]:
        return sorted(self._spiders)

    def items(self) -> Iterator[tuple[str, type[Spider]]]:
        return iter(self._spiders.items())

    def __contains__(self, name: object) -> bool:
        return name in self._spiders

    def __len__(self) -> int:
        return len(self._spiders)

    def __iter__(self) -> Iterator[type[Spider]]:
        return iter(self._spiders.values())

    # ------------------------------------------------------------------ 发现
    @classmethod
    def discover(cls, target: str | Path, recursive: bool = True) -> "SpiderRegistry":
        """从目录、单个 .py 文件或包名中发现 Spider。

        目录/包内所有模块都会被导入，收集其中定义（而非导入）的 Spider 子类。
        """
        path = Path(str(target)).expanduser()
        registry = cls()

        if path.exists():
            if path.is_dir():
                files = sorted(path.rglob("*.py") if recursive else path.glob("*.py"))
            else:
                files = [path]
            for file in files:
                if file.name.startswith("_"):
                    continue
                registry._register_module(_import_file(file))
            return registry

        try:
            module = importlib.import_module(str(target))
        except ImportError as exc:
            raise SpiderNotFound(f"无法导入模块 {target}: {exc}") from exc

        package_path = getattr(module, "__path__", None)
        if package_path:
            registry._register_module(module)
            for info in pkgutil.walk_packages(module.__path__, prefix=f"{module.__name__}."):
                if info.name.rsplit(".", 1)[-1].startswith("_"):
                    continue
                registry._register_module(importlib.import_module(info.name))
        else:
            registry._register_module(module)
        return registry

    def _register_module(self, module: object) -> None:
        module_name = getattr(module, "__name__", "")
        for value in vars(module).values():
            if not (isinstance(value, type) and issubclass(value, Spider)):
                continue
            if value is Spider or getattr(value, "__module__", None) != module_name:
                continue
            self.register(value)


def _import_file(path: Path) -> object:
    module_name = f"_cc_crawl_spider_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise SpiderNotFound(f"无法加载爬虫文件：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module
