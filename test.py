# -*- coding: utf-8 -*-
# @Time    : 2026/3/18 20:45
# @Author  : mahuaqiu
# @File    : test.py
# 
#
import requests
import base64


def ocr_recognize(image_path,img2, api_url):
    """
    发送 OCR 识别请求

    Args:
        image_path (str): 图片文件路径
        api_url (str): OCR 识别接口地址

    Returns:
        dict: API 响应结果
    """
    # 读取图片文件并进行 Base64 编码
    with open(image_path, "rb") as image_file:
        encoded_image = base64.b64encode(image_file.read()).decode("utf-8")

    with open(img2, "rb") as image_file:
        encoded_image2 = base64.b64encode(image_file.read()).decode("utf-8")

    # 构建请求数据
    payload = {
        "image": encoded_image,
        "filter_text":"雨"
    }
    payload = {
        "source_image": encoded_image,
        "template_image":encoded_image2,
        # "filter_text":"reg_[管d]家"
    }

    # 发送 POST 请求
    headers = {
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(api_url, json=payload, headers=headers)
        response.raise_for_status()  # 检查 HTTP 错误
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"请求失败: {e}")
        return None


# 使用示例
if __name__ == "__main__":
    # 替换为你的 API 地址
    API_URL = "http://192.168.0.102:9021/ocr/get_coord_by_text"
    API_URL = "http://192.168.0.102:9021/ocr/get_ocr_texts"
    API_URL = "http://192.168.0.102:9021/image/match_near_text"
    API_URL = "http://192.168.0.102:9021/image/match"

    # 替换为你的图片路径
    IMAGE_PATH = "/Users/ma/Downloads/login.png"
    IMAGE_PATH2 = "/Users/ma/Downloads/登录同意-勾选框.png"

    result = ocr_recognize(IMAGE_PATH,IMAGE_PATH2, API_URL)
    if result:
        print("OCR 识别结果:", result)