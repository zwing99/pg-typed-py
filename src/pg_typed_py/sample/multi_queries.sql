/*
name=get_all_users
*/
SELECT * FROM users;

/*
name=get_user_by_email
*/
SELECT * FROM users WHERE email = :email;

/*
name=get_users_created_after
*/
SELECT id, email, created_at
FROM users
WHERE created_at > :created_after
ORDER BY created_at DESC;
