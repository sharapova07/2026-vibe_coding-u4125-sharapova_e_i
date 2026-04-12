University: [ITMO University](https://itmo.ru/ru/)  
Faculty: [FICT](https://fict.itmo.ru)  
Course: [Vibe Coding: AI-боты для бизнеса](https://github.com/itmo-ict-faculty/vibe-coding-for-business)   
Year: 2025/2026  
Group: U4125    
Author: Sharapova Elizaveta Igorevna  
Lab: Lab3  
Date of create: 09.04.2026  
Date of finished:

# Лабораторная работа №3
## "Запуск бота для реального использования""
**Описание:**   
в ходе работы созданный бот будет развернут так, чтобы им могли пользоваться реальные люди - коллеги, друзья или команда. Это последний шаг от идеи до работающего продукта.  
**Цель работы:**  
научиться деплоить бота и собирать обратную связь от реальных пользователей для улучшения продукта.  

---

### Ход работы
**1. Выбор способа деплоя:**    
Для деплоя был выбран облачный хостинг Railway. Также для удобства развертывания был создан отдельный репозиторий на GitHub: [Vibe-Coding_lab3](https://github.com/sharapova07/Vibe-Coding_lab3.git) 
![3-0](https://github.com/sharapova07/2026-vibe_coding-u4125-sharapova_e_i/blob/487406587b3337d22bdbbe27839ebc00cbcb2130/lab3/screenshots3/(3-0).png)

**2. Подготовка к деплою:**   
Перед деплоем был проверен код и локальная работа бота, а также наличие токена в переменном окружении. Далее были созданы `.gitignore` и `requirements.txt`  
.gitignore  
```
.DS_Store
__pycache__/
*.pyc
.venv/
venv/
.env
.vscode/
.idea/
*.log
```
requirements.txt
```
python-telegram-bot[job-queue]==21.11.1
python-dotenv==1.2.1
requests==2.31.0
```
**3. Деплой:**   
После регистрации на Railway и входа через GitHub был создан новый проект.   
Далее был подключен мой репозиторий:  
![3-1](https://github.com/sharapova07/2026-vibe_coding-u4125-sharapova_e_i/blob/487406587b3337d22bdbbe27839ebc00cbcb2130/lab3/screenshots3/(3-1).png)
И вставлен токен бота `Ally For Projects`   
![3-2](https://github.com/sharapova07/2026-vibe_coding-u4125-sharapova_e_i/blob/487406587b3337d22bdbbe27839ebc00cbcb2130/lab3/screenshots3/(3-2).png)

Деплой и запуск бота:    
![3-3](https://github.com/sharapova07/2026-vibe_coding-u4125-sharapova_e_i/blob/487406587b3337d22bdbbe27839ebc00cbcb2130/lab3/screenshots3/(3-3).png)

**4. Тестирование бота:**   
После деплоя была проверена работа бота в Telegram. Все функции исправны и стабильно работают.  
![3-4](https://github.com/sharapova07/2026-vibe_coding-u4125-sharapova_e_i/blob/902d0b15e6f2192d6b9682f06e7b013066ca6155/lab3/screenshots3/(3-4).png)
![3-5](https://github.com/sharapova07/2026-vibe_coding-u4125-sharapova_e_i/blob/5cf093fe701d54d48a1bea8dec1e399f062aed83/lab3/screenshots3/(3-5).png)
![3-6](https://github.com/sharapova07/2026-vibe_coding-u4125-sharapova_e_i/blob/5cf093fe701d54d48a1bea8dec1e399f062aed83/lab3/screenshots3/(3-6).png)

**5. Сбор обратной связи:**  
Для сбора обратной связи было приглашено несколько членов проектной команды, в том числе руководитель проекта, обладающий правами администратора в боте. 

```
Обратная связь от руководителя проекта:  
```
Скриншоты использования:  
Фидбек:  

### Результаты лабораторной работы  
В результате данной работы:    
- Telegram-бот `Ally For Projects` развернут и доступен для использования
- собрана обратная связь от реальных пользователей с разными правами доступа
- внесены улучшения на основе фидбека
- бот стабильно работает
- написан отчет по проделанной работе с описанием выполненных задач и скриншотами
