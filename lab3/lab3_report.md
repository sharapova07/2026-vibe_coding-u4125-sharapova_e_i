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
Для сбора обратной связи было приглашено несколько членов проектной команды, в том числе руководитель проекта, обладающий правами администрирования в боте. 

```
Обратная связь от руководителя проекта:  
```
Описание:  
В ходе тестирования были испробованы все команды, включая те, на которые есть доступ только у администратора и руководителя проекта, например, `/add_event` и `/del_event`. Все функции работают исправно и бот выполняет свое предназначение.   

Фидбек от РП:  
```
Бот удобный, легко добавлять события и смотреть текущие мероприятия проектной команды.
Особенно понравилось, что эти мероприятия разделены на дни и недели.
Также было бы здорово, если при добавлении события можно было вернуться на предыдущий этап.
```
Скриншоты использования:  

**6. Улучшения на основе фидбека:**  
На основе обратной связи было выявлено, что не хватает дополнительной кнопки `назад`, чтобы вернуться к предыдущему этапу при добавлении мероприятия через команду `/add_event`. В связи с чем через Cursor и redeploy в Railway был доработан бот `Ally For Projects`  
Внесение изменений:    
![(3) Внесение изменений](https://github.com/sharapova07/2026-vibe_coding-u4125-sharapova_e_i/blob/b4e668a80ee378678b17db39e93313237f7b8cdb/lab3/screenshots3/(3)%20%D0%92%D0%BD%D0%B5%D1%81%D0%B5%D0%BD%D0%B8%D0%B5%20%D0%B8%D0%B7%D0%BC%D0%B5%D0%BD%D0%B5%D0%BD%D0%B8%D0%B8%CC%86.png) 
Работающая кнопка `назад` в боте:    
![(3) Добавление кнопки назад](https://github.com/sharapova07/2026-vibe_coding-u4125-sharapova_e_i/blob/b4e668a80ee378678b17db39e93313237f7b8cdb/lab3/screenshots3/(3)%20%D0%94%D0%BE%D0%B1%D0%B0%D0%B2%D0%BB%D0%B5%D0%BD%D0%B8%D0%B5%20%D0%BA%D0%BD%D0%BE%D0%BF%D0%BA%D0%B8%20%D0%BD%D0%B0%D0%B7%D0%B0%D0%B4%20.png)

### Результаты лабораторной работы  
В результате данной работы:    
- Telegram-бот `Ally For Projects` развернут и доступен для использования
- собрана обратная связь от реальных пользователей с разными правами доступа
- внесены улучшения на основе фидбека
- бот стабильно работает
- написан отчет по проделанной работе с описанием выполненных задач и скриншотами
