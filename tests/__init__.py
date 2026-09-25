# -*- coding: utf-8 -*-
"""测试包。

本目录下所有用例都遵循三条铁律（见 CONTRIBUTING.md）：
1. 不往仓库里写文件（数据根目录由 NFO_TAG_FIXER_HOME 重定向到临时目录）；
2. 模态框一律打桩（离屏下 QMessageBox.exec() 会把 pytest 永久挂住）；
3. 经过后台线程的路径用 sync_worker 同步执行，避免时序不确定。
"""
