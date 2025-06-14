/*
name=get_user_by_id
*/
SELECT * FROM users WHERE id = :user_id;

/*
name=search_users_by_email_pattern
*/
SELECT * FROM users WHERE email LIKE :email_pattern;

/*
name=get_users_count
*/
SELECT COUNT(*) as total_count FROM users WHERE created_at > :since_date;
