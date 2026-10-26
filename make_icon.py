"""Draws icon.ico: the black island pill with green sound bars. Run once: python make_icon.py"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

app = QGuiApplication([])
img = QImage(256, 256, QImage.Format_ARGB32)
img.fill(Qt.transparent)
p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)
p.setPen(Qt.NoPen)
p.setBrush(QColor('#000000'))
p.drawRoundedRect(QRectF(8, 78, 240, 100), 50, 50)
p.setBrush(QColor('#30d158'))
for x, h in ((150, 40), (170, 64), (190, 30), (210, 52)):
    p.drawRoundedRect(QRectF(x, 128 - h / 2, 11, h), 5.5, 5.5)
p.setBrush(QColor('#ffffff'))
p.drawEllipse(QRectF(40, 106, 44, 44))
p.end()
assert img.save('icon.ico'), 'Qt has no ICO writer'
