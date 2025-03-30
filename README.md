
# vAITony 

This is a work-in-progress Django project designed to serve as a **starting point** for more complex web applications. 
The project aims to reduce development time for applications requiring a dashboard or web pages based on the AdminLTE 
template. 
Several initial features — typically found in complex web applications — are already implemented and **ready 
to use** reducing the development process allowing developers to focus on implementing custom features specific 
to their projects.


## Pre-configured Features:
- **Django AdminLTE Integration**: Integration of AdminLTE for a modern, responsive UI.
- **User Authentication System**: Includes registration, login, password change, password reset, and logout functionalities.
- **MySQL Database Setup**: The project uses **MySQL** as the default database, with configurations ready in `settings.py`.
- **Pre-built Dashboard Example**: A functional dashboard is included as a starting point for your project.
- **User Management**: Complete user authentication system is included:
  - Registration
  - Login
  - Password change
  - Password reset (via email)
  - Logout


## Tech Stack

- **Django** Web Framework
- **AdminLTE** ( Version 4.0.0-beta3 with sources)
- **MySQL** (or any database management)

## Disclaimer

This project is **not complete** and is intended to evolve over time. As a starting template, it may not include all
the functionality required for a production-level application. Developers are encouraged to modify and extend it 
according to their needs. Please note that the current state of the project is subject to changes, and 
**no guarantees or liabilities** are provided regarding its functionality or security.



## Installation Instructions

### 1. Clone the Project

First, clone the project repository from the following URL:

```bash
git clone https://github.com/giuseppeciuni/django-adminlte-starter.git
```


### 2. Database Configuration 
Change DB configuration in settings.py file. Below the configuration that must be updated with you own database configuration.
Right now this configuration is based on MySQL configuration anyway it can be changed with other DB Engines.

```
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'your_database_name',
        'USER': 'your_database_user',
        'PASSWORD': 'your_password',
        'HOST': 'localhost',
        'PORT': '3306',
    }
}
```

### 3. Set Up a virtual environment (Recommended)

Create and activate a virtual environment using venv:


#### On Linux/macOS:
```
python3 -m venv venv
source venv/bin/activate
```

#### On Windows:
```
python -m venv venv
source venv\Scripts\activate
```


#### Install dependencies
```
pip install -r requirements.txt
```


### 4. Create logs folder (in order to collect all application logs)
```
mkdir logs
```




### 5. Database Setup and Migrations

Before applying the migrations you need to create a new database in your MySQL instance. Make sure the database 
name matches the one defined in your `settings.py` file.

1. **Create the Database**  
   Log in to your MySQL server and create the database using the following command:

   ```sql
   CREATE DATABASE your_database_name;
    ```
    Replace **your_database_name*** with the desired name for your database.   


2. **Apply database Migration**
   ```
   python manage.py migrate
   ```
   This command will create all the required tables in the MySQL database used in the project.






### 6. Create a superuser (a Django root user)

```
python manage.py createsuperuser
```




### 7. Initial Setup for User Roles

```
python manage.py initial_setup_profile

```




### 8. Create the collectstatic
```
python manage.py collectstatic
```



### 9. Run the project
```
python manage.py runserver
```



## Screenshots


<p align="center">
    <img src="github_screenshots/img_4.png" alt="Screenshot 1" />
    <img src="github_screenshots/img.png" alt="Screenshot 2" />
    <img src="github_screenshots/img_5.png" alt="Screenshot 3" />
</p>

## Contributions and Further Development
As this project is a **Work-In-Progress**, contributions are super welcome. Feel free to fork the repository, submit pull 
requests and suggest improvements. Keep in mind that changes may occur over time and additional features may be added 
based on feedback and requirements.



## Credits

This project was built using the following technologies and resources:

- **[Django](https://www.djangoproject.com/)** – The high-level Python web framework that encourages rapid development and clean, pragmatic design.
- **[AdminLTE](https://adminlte.io/)** – A fully responsive admin template for building dashboards and web applications.
- **[MySQL](https://www.mysql.com/)** – The world's most popular open-source database used for data storage in this project.




## Support

If you need help, feel free to start a discussion in the project's repository. For commercial support, please contact 
Giuseppe Ciuni at '**giuseppe.ciuni** @ **gmail.com**' 
